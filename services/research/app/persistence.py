"""Persist research packets into Postgres (web_research_runs / documents / evidence)."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from contracts import ResearchPacket

from .config import config

logger = logging.getLogger("research.persistence")


def _event_uuid(event_id: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(event_id))
    except (ValueError, TypeError):
        return None


def _open_run(session, event_uuid: uuid.UUID):
    from db.models.research import WebResearchRun

    return (
        session.query(WebResearchRun)
        .filter(
            WebResearchRun.event_id == event_uuid,
            WebResearchRun.status.in_(("queued", "running")),
        )
        .order_by(WebResearchRun.created_at.desc())
        .first()
    )


def load_completed_evidence(event_id: str) -> Optional[list]:
    """Evidence already stored for a finished search. None if this match was not searched yet."""
    if not config.persist_to_db:
        return None
    event_uuid = _event_uuid(event_id)
    if event_uuid is None:
        return None
    try:
        from db.connection import SessionLocal
        from db.models.research import WebDocument, WebEvidence, WebResearchRun
    except ImportError:
        return None
    try:
        with SessionLocal() as session:
            run = (
                session.query(WebResearchRun)
                .filter(
                    WebResearchRun.event_id == event_uuid,
                    WebResearchRun.status == "completed",
                )
                .order_by(WebResearchRun.created_at.desc())
                .first()
            )
            if run is None:
                return None
            rows = (
                session.query(WebEvidence, WebDocument)
                .join(WebDocument, WebEvidence.document_id == WebDocument.id)
                .filter(WebEvidence.event_id == event_uuid)
                .all()
            )
            return [
                {
                    "url": doc.url,
                    "title": doc.title,
                    "domain": doc.source_domain,
                    "snippet": evidence.snippet,
                    "content_hash": doc.content_hash,
                    "published_at": doc.published_at,
                    "retrieved_at": doc.fetched_at,
                }
                for evidence, doc in rows
            ]
    except Exception as exc:
        logger.warning("Failed to read stored research for %s: %s", event_id, exc)
        return None


def begin_research_run(event_id: str, query: Optional[str] = None) -> None:
    """Marks the match search as running so the queue can show that work has started."""
    if not config.persist_to_db:
        return
    event_uuid = _event_uuid(event_id)
    if event_uuid is None:
        return
    try:
        from db.connection import SessionLocal
        from db.models.research import WebResearchRun
    except ImportError as exc:
        logger.warning("DB package unavailable, research not persisted: %s", exc)
        return

    run_query = (query or "research")[:255]
    try:
        with SessionLocal() as session:
            run = _open_run(session, event_uuid)
            if run is None:
                run = WebResearchRun(
                    id=uuid.uuid4(),
                    event_id=event_uuid,
                    query=run_query,
                    status="running",
                    documents_found=0,
                )
                session.add(run)
            else:
                run.status = "running"
                if query:
                    run.query = run_query
            session.commit()
    except Exception as exc:
        logger.warning("Failed to mark research running for %s: %s", event_id, exc)


def persist_research_packet(packet: ResearchPacket, query: Optional[str] = None) -> None:
    """
    Writes packet evidence into neurobet DB so the frontend / MCP can show sources.
    An empty packet still closes the run: the match was searched and nothing was found.
    No-op for non-UUID event ids (unit tests) or when DB is unavailable.
    """
    if not config.persist_to_db:
        return

    event_uuid = _event_uuid(packet.event_id)
    if event_uuid is None:
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
            run = _open_run(session, event_uuid)
            found = len(packet.evidence)
            if run is None:
                run = WebResearchRun(
                    id=uuid.uuid4(),
                    event_id=event_uuid,
                    query=run_query,
                    status="completed",
                    documents_found=found,
                )
                session.add(run)
            else:
                run.status = "completed"
                run.documents_found = found
                if query:
                    run.query = run_query
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
