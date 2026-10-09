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
from .search_backend import SearchBackend
from .storage import ResearchStorage
from .url_normalizer import URLNormalizer

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
        queries = self.search_backend.generate_tennis_queries(
            request.participant_a, request.participant_b, request.tournament
        )
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

        logger.info(f"Executing network research for event '{request.event_id}' (query: '{primary_query}')")

        # 2. Search for URLs
        candidate_urls: List[str] = []
        for q in queries:
            urls = self.search_backend.search(q, max_results=request.max_pages)
            candidate_urls.extend(urls)

        # 3. Process and fetch candidate pages up to max_pages
        seen_urls: Set[str] = set()
        seen_hashes: Set[str] = set()
        evidence_list: List[ResearchEvidence] = []

        for raw_url in candidate_urls:
            if len(evidence_list) >= request.max_pages:
                break

            normalized_url = URLNormalizer.normalize(raw_url)
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)

            # Domain filter check
            allowed, reason = DomainFilter.is_allowed(normalized_url)
            if not allowed:
                continue

            # Sandboxed fetch
            html, fetch_status = self.fetcher.fetch(normalized_url, method="GET")
            if not html or fetch_status not in ("OK", "MOCK_OK"):
                continue

            # HTML extraction
            doc = HTMLExtractor.extract_document(
                html, max_text_size=config.max_text_size
            )

            # Content hash deduplication
            content_hash = doc["content_hash"]
            if content_hash in seen_hashes:
                logger.info(f"Duplicate document content hash '{content_hash}' skipped.")
                continue
            seen_hashes.add(content_hash)

            # Archive raw snapshot to MinIO S3
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
                },
            )

            # Create strictly non-authoritative evidence contract
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
                relevance_score=0.90,
                freshness_score=doc["freshness_score"],
                is_live_score_authority=False,  # CRITICAL ARCHITECTURAL RULE
            )
            evidence_list.append(ev)

        # 4. Formulate summary
        summary = (
            f"Collected {len(evidence_list)} external sources for {request.participant_a} vs {request.participant_b}."
            if evidence_list
            else "No external research found or accessible."
        )

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

        # 5. Save to cache
        self.cache.set(cache_key, packet)
        self.cache.record_run(
            event_id=request.event_id,
            query=primary_query,
            packet_id=packet.packet_id,
            evidence_count=len(packet.evidence),
            cache_hit=False,
        )

        return packet
