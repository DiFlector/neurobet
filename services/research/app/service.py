"""Web Research pipeline coordinator."""

from datetime import datetime, timezone
import logging
import uuid
from typing import List, Set

from contracts import ResearchEvidence, ResearchPacket, ResearchRequest
from .cache import ResearchCache
from .config import config
from .domain_filter import DomainFilter
from .fetcher import SandboxedFetcher
from .html_extractor import HTMLExtractor
from .persistence import begin_research_run, load_completed_evidence, persist_research_packet
from .relevance import score_relevance, should_keep
from .search_backend import SearchBackend
from .storage import ResearchStorage
from .url_normalizer import URLNormalizer
from .url_priority import prioritize_urls, split_match_hubs

logger = logging.getLogger("research.service")


class WebResearchService:
    """Coordinates search, sandboxed fetching, HTML extraction, and caching."""

    def __init__(
        self,
        fetcher: SandboxedFetcher = None,
        search_backend: SearchBackend = None,
        cache: ResearchCache = None,
        storage: ResearchStorage = None,
    ):
        self.fetcher = fetcher or SandboxedFetcher()
        self.search_backend = search_backend or SearchBackend()
        self.cache = cache or ResearchCache()
        self.storage = storage or ResearchStorage()

    def execute_research(self, request: ResearchRequest) -> ResearchPacket:
        """
        Executes end-to-end qualitative web research pipeline for a sporting event.
        Guarantees that all evidence is marked is_live_score_authority=False.
        """
        queries = self.search_backend.generate_queries(
            request.sport_code,
            request.participant_a,
            request.participant_b,
            request.tournament,
        )
        # hybrid: три запроса о снятии и травме, без карточки матча.
        if self.search_backend.backend in ("hybrid", "both", "all"):
            queries = queries[:3]
        primary_query = queries[0] if queries else f"{request.participant_a} vs {request.participant_b}"

        cache_key = self.cache.generate_cache_key(request.event_id, primary_query)

        # 1. Check cache (unless force_refresh is requested)
        if not request.force_refresh:
            cached_packet = self.cache.get(cache_key)
            if cached_packet:
                self.cache.record_run(
                    event_id=request.event_id,
                    query=primary_query,
                    packet_id=cached_packet.packet_id,
                    evidence_count=len(cached_packet.evidence),
                    cache_hit=True,
                )
                return cached_packet

        if not request.force_refresh:
            stored = load_completed_evidence(request.event_id)
            if stored is not None:
                evidence = [
                    ResearchEvidence(
                        evidence_id=f"ev_{uuid.uuid4().hex[:12]}",
                        url=item["url"] or "",
                        normalized_url=item["url"] or "",
                        domain=item["domain"] or "unknown",
                        title=item["title"] or "Source",
                        published_at=item["published_at"],
                        retrieved_at=item["retrieved_at"] or datetime.now(timezone.utc),
                        snippet=item["snippet"] or "",
                        content_hash=item["content_hash"] or "stored",
                        is_live_score_authority=False,
                    )
                    for item in stored
                    if (item.get("snippet") or "").strip()
                ]
                return ResearchPacket(
                    packet_id=f"pkt_{uuid.uuid4().hex[:12]}",
                    event_id=request.event_id,
                    sport_code=request.sport_code,
                    query=primary_query,
                    evidence=evidence,
                    summary="Search already completed for this match.",
                    cached=True,
                    created_at=datetime.now(timezone.utc),
                )

        logger.info(f"Executing network research for event '{request.event_id}' (query: '{primary_query}')")
        begin_research_run(request.event_id, primary_query)

        # 2. Search for URLs (hybrid = SearXNG∥DDG → общий пул)
        candidate_urls: List[str] = []
        for q in queries:
            urls = self.search_backend.search(q, max_results=max(request.max_pages, 5))
            candidate_urls.extend(urls)

        preferred = list(getattr(self.search_backend, "PRIORITY_DOMAINS", ()))
        candidate_urls = prioritize_urls(candidate_urls, preferred)
        hubs, rest = split_match_hubs(candidate_urls)
        # Новости о снятии раньше карточек матча.
        ordered_urls = rest + hubs
        ordered_urls = ordered_urls[: max(request.max_pages * 5, 10)]
        if hubs:
            logger.info(
                "Match hub priority: %d page(s) first (e.g. %s)",
                len(hubs),
                hubs[0][:90],
            )

        # 3. Fetch: hubs first, then fallback articles
        seen_urls: Set[str] = set()
        seen_hashes: Set[str] = set()
        scored: List[tuple] = []  # (relevance, freshness, is_hub, ResearchEvidence)

        def _ingest(raw_url: str) -> None:
            if len(scored) >= request.max_pages * 2:
                return
            normalized_url = URLNormalizer.normalize(raw_url)
            if normalized_url in seen_urls:
                return
            seen_urls.add(normalized_url)

            allowed, _reason = DomainFilter.is_allowed(normalized_url)
            if not allowed:
                return

            html, fetch_status = self.fetcher.fetch(normalized_url, method="GET")
            if not html or fetch_status not in ("OK", "MOCK_OK"):
                return

            doc = HTMLExtractor.extract_document(
                html, max_text_size=config.max_text_size
            )

            content_hash = doc["content_hash"]
            if content_hash in seen_hashes:
                logger.info(f"Duplicate document content hash '{content_hash}' skipped.")
                return
            seen_hashes.add(content_hash)

            rel, meta = score_relevance(
                title=doc["title"] or "",
                snippet=doc["snippet"] or "",
                url=raw_url,
                participant_a=request.participant_a,
                participant_b=request.participant_b,
            )
            if not should_keep(rel, meta):
                logger.info(
                    "Drop low-relevance evidence (%.2f noise=%s hub=%s a=%s b=%s): %s",
                    rel,
                    meta.get("noise"),
                    meta.get("match_hub"),
                    meta.get("hit_a"),
                    meta.get("hit_b"),
                    (doc["title"] or "")[:80],
                )
                return

            domain = DomainFilter.extract_domain(normalized_url)
            self.storage.archive_document(
                event_id=request.event_id,
                content_hash=content_hash,
                document_data={
                    "event_id": request.event_id,
                    "url": raw_url,
                    "normalized_url": normalized_url,
                    "domain": domain,
                    "title": doc["title"],
                    "text": doc["text"],
                    "published_at": doc["published_at"],
                    "retrieved_at": doc["retrieved_at"],
                    "relevance_score": rel,
                    "relevance_meta": meta,
                },
            )

            ev = ResearchEvidence(
                evidence_id=f"ev_{uuid.uuid4().hex[:12]}",
                url=raw_url,
                normalized_url=normalized_url,
                domain=domain,
                title=doc["title"],
                published_at=doc["published_at"],
                retrieved_at=doc["retrieved_at"],
                age_seconds=doc["age_seconds"],
                snippet=doc["snippet"],
                content_hash=content_hash,
                relevance_score=round(rel, 4),
                freshness_score=float(doc["freshness_score"] or 0.0),
                is_live_score_authority=False,  # CRITICAL ARCHITECTURAL RULE
            )
            scored.append(
                (rel, float(doc["freshness_score"] or 0.0), ev)
            )

        for raw_url in rest:
            _ingest(raw_url)
        if len(scored) < request.max_pages:
            for raw_url in hubs:
                if len(scored) >= request.max_pages * 2:
                    break
                _ingest(raw_url)

        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        evidence_list = [row[2] for row in scored[: request.max_pages]]

        # Check if external predictions exist for this event
        try:
            from .predictions.manager import PredictionManager
            consensus = PredictionManager().get_consensus_for_event(request.event_id)
            if consensus and consensus.total_predictions > 0:
                pred_snippet = (
                    f"Консенсус внешних прогнозистов ({len(consensus.sources)} источников: {', '.join(consensus.sources[:4])}): "
                    f"Всего прогнозов: {consensus.total_predictions}. "
                    f"За {request.participant_a}: {consensus.p1_count} ({consensus.p1_share*100:.0f}%), "
                    f"За {request.participant_b}: {consensus.p2_count} ({consensus.p2_share*100:.0f}%). "
                    f"Консенсус: {consensus.consensus_pick.upper()} (уверенность {consensus.consensus_confidence:.2f})."
                )
                if consensus.expert_snippets:
                    pred_snippet += " Выдержки экспертов: " + " | ".join(consensus.expert_snippets[:2])

                consensus_evidence = ResearchEvidence(
                    evidence_id=f"ev_pred_{uuid.uuid4().hex[:8]}",
                    url="internal://predictions/consensus",
                    normalized_url="internal://predictions/consensus",
                    domain="expert_consensus",
                    title=f"Консенсус прогнозов на матч ({consensus.total_predictions} источников)",
                    published_at=datetime.now(timezone.utc),
                    retrieved_at=datetime.now(timezone.utc),
                    age_seconds=0,
                    snippet=pred_snippet[:1000],
                    content_hash=f"hash_consensus_{request.event_id}",
                    relevance_score=1.0,
                    freshness_score=1.0,
                    is_live_score_authority=False,
                )
                evidence_list.insert(0, consensus_evidence)
        except Exception as e:
            logger.debug(f"Could not load prediction consensus for research: {e}")


        # 4. Formulate summary
        if evidence_list:
            avg_rel = sum(float(e.relevance_score) for e in evidence_list) / len(evidence_list)
            summary = (
                f"Collected {len(evidence_list)} relevant sources "
                f"(avg relevance {avg_rel:.2f}) for "
                f"{request.participant_a} vs {request.participant_b}."
            )
        else:
            summary = "No external research found or accessible."

        packet = ResearchPacket(
            packet_id=f"pkt_{uuid.uuid4().hex[:12]}",
            event_id=request.event_id,
            sport_code=request.sport_code,
            query=primary_query,
            evidence=evidence_list,
            summary=summary,
            cached=False,
            created_at=datetime.now(timezone.utc),
        )

        # 5. Save to cache + Postgres (для UI «Источники и новости»)
        self.cache.set(cache_key, packet)
        self.cache.record_run(
            event_id=request.event_id,
            query=primary_query,
            packet_id=packet.packet_id,
            evidence_count=len(packet.evidence),
            cache_hit=False,
        )
        persist_research_packet(packet, query=primary_query)

        return packet
