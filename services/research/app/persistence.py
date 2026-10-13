"""Persist research packets into Postgres (web_research_runs / documents / evidence)."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from contracts import ResearchPacket

from .config import config

logger = logging.getLogger("research.persistence")


def persist_research_packet(packet: ResearchPacket, query: Optional[str] = None) -> None:
    """
    Writes packet evidence into neurobet DB so the frontend / MCP can show sources.
    No-op for non-UUID event ids (unit tests) or when DB is unavailable.
    """
    if not config.persist_to_db:
        return
    if not packet.evidence:
        # Auditable empty run still useful? Skip noise from stubs.
        return

    try:
        event_uuid = uuid.UUID(str(packet.event_id))
    except (ValueError, TypeError):
        logger.debug("Skip DB persist: event_id=%s is not a UUID", packet.event_id)
        return

    try:
        from db.connection import SessionLocal
        from db.models.research import WebDocument, WebEvidence, WebResearchRun
    except ImportError as exc:
        logger.warning("DB package unavailable, research not persisted: %s", exc)
        return

    run_query = (query or packet.query or "research")[:255]

    try:
        with SessionLocal() as session:
            run = WebResearchRun(
                id=uuid.uuid4(),
                event_id=event_uuid,
                query=run_query,
                status="completed",
                documents_found=len(packet.evidence),
            )
            session.add(run)
            session.flush()

            for ev in packet.evidence:
                content_hash = (ev.content_hash or "")[:64]
                # Skip duplicate content already linked to this event.
                exists = (
                    session.query(WebEvidence)
                    .join(WebDocument, WebEvidence.document_id == WebDocument.id)
                    .filter(
                        WebEvidence.event_id == event_uuid,
                        WebDocument.content_hash == content_hash,
                    )
                    .first()
                )
                if exists:
                    continue

                doc = WebDocument(
                    id=uuid.uuid4(),
                    run_id=run.id,
                    url=(ev.url or "")[:512],
                    title=(ev.title or ev.domain or "Source")[:255],
                    source_domain=(ev.domain or "unknown")[:128],
                    content_hash=content_hash or f"missing:{uuid.uuid4().hex[:12]}",
                    published_at=ev.published_at,
                    fetched_at=ev.retrieved_at,
                    raw_storage_path=None,
                )
                session.add(doc)
                session.flush()

                session.add(
                    WebEvidence(
                        id=uuid.uuid4(),
                        document_id=doc.id,
                        event_id=event_uuid,
                        snippet=(ev.snippet or "")[:2048],
                        relevance_score=float(ev.relevance_score or 0.5),
                        freshness_score=float(ev.freshness_score or 0.5),
                    )
                )

            session.commit()
            logger.info(
                "Persisted %d research evidence row(s) for event %s",
                len(packet.evidence),
                packet.event_id,
            )
    except Exception as exc:
        logger.warning("Failed to persist research for %s: %s", packet.event_id, exc)
