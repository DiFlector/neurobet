"""Persistent web-search queue for live and prematch matches."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("backend.research_queue")

SEARCH_LIMIT = 3
_sync_lock = threading.Lock()
_inflight: set[str] = set()


def display_status(has_evidence: bool, run_status: Optional[str], documents_found: int = 0) -> str:
    """UI state for one match: ready, running, empty, or queued."""
    if has_evidence:
        return "ready"
    if run_status == "running":
        return "running"
    if run_status == "completed":
        return "empty"
    return "queued"


def _models():
    from db.connection import SessionLocal
    from db.models.events import Event
    from db.models.research import WebEvidence, WebResearchRun

    return SessionLocal, Event, WebResearchRun, WebEvidence


def _complete_empty(event_id: str) -> None:
    """Closes a search that never returned a packet so it is not retried forever."""
    try:
        SessionLocal, _, WebResearchRun, _ = _models()
        event_uuid = uuid.UUID(str(event_id))
    except Exception:
        return
    try:
        with SessionLocal() as session:
            run = (
                session.query(WebResearchRun)
                .filter(
                    WebResearchRun.event_id == event_uuid,
                    WebResearchRun.status.in_(("queued", "running")),
                )
                .order_by(WebResearchRun.created_at.desc())
                .first()
            )
            if run is None:
                return
            run.status = "completed"
            run.documents_found = 0
            session.commit()
    except Exception as exc:
        logger.warning("Failed to close empty research for %s: %s", event_id, exc)


def _call_research(event_id: str, sport_code: str, participant_a: str, participant_b: str) -> None:
    url = os.getenv("RESEARCH_URL", "http://research:8000/api/research")
    payload = json.dumps(
        {
            "event_id": event_id,
            "sport_code": sport_code or "tennis",
            "participant_a": participant_a or "Participant A",
            "participant_b": participant_b or "Participant B",
            "max_pages": 5,
        }
    ).encode("utf-8")
    req = Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=70) as resp:
            resp.read()
    except (URLError, TimeoutError, OSError) as exc:
        logger.warning("Research request failed for %s: %s", event_id, exc)
        _complete_empty(event_id)
    except Exception as exc:
        logger.warning("Research request failed for %s: %s", event_id, exc)
        _complete_empty(event_id)
    finally:
        _inflight.discard(event_id)


def research_counts() -> Dict[str, int]:
    try:
        SessionLocal, _, WebResearchRun, _ = _models()
    except Exception:
        return {"queued": 0, "running": 0, "completed": 0}
    with SessionLocal() as session:
        rows = session.query(WebResearchRun.status).all()
    queued = sum(1 for (status,) in rows if status == "queued")
    running = sum(1 for (status,) in rows if status == "running")
    completed = sum(1 for (status,) in rows if status == "completed")
    return {"queued": queued, "running": running, "completed": completed}


def list_search_queue() -> List[Dict[str, Any]]:
    """Flat rows for the scheduler tab: waiting and in-progress searches."""
    try:
        SessionLocal, Event, WebResearchRun, _ = _models()
    except Exception:
        return []
    with SessionLocal() as session:
        rows = (
            session.query(WebResearchRun, Event)
            .join(Event, Event.id == WebResearchRun.event_id)
            .filter(WebResearchRun.status.in_(("queued", "running")))
            .order_by(WebResearchRun.created_at.asc())
            .all()
        )
        items = []
        for run, event in rows:
            name_a = event.participant_a_name or "A"
            name_b = event.participant_b_name or "B"
            items.append(
                {
                    "event_id": str(event.id),
                    "label": f"{name_a} — {name_b}",
                    "composite_score": 0,
                    "edge": 0,
                    "confidence": 0,
                    "status": "PROCESSING" if run.status == "running" else "QUEUED",
                }
            )
        return items


def match_research_status(event_id: str) -> Dict[str, str]:
    try:
        event_uuid = uuid.UUID(str(event_id))
        SessionLocal, _, WebResearchRun, WebEvidence = _models()
    except Exception:
        return {"status": "queued"}
    with SessionLocal() as session:
        evidence = (
            session.query(WebEvidence.id)
            .filter(WebEvidence.event_id == event_uuid)
            .first()
        )
        run = (
            session.query(WebResearchRun)
            .filter(WebResearchRun.event_id == event_uuid)
            .order_by(WebResearchRun.created_at.desc())
            .first()
        )
        status = display_status(
            has_evidence=evidence is not None,
            run_status=run.status if run else None,
            documents_found=int(run.documents_found or 0) if run else 0,
        )
        return {"status": status}


def sync_search_queue(limit: int = SEARCH_LIMIT) -> Dict[str, Any]:
    """Enqueue live/prematch matches once, then start at most `limit` searches."""
    if not _sync_lock.acquire(blocking=False):
        counts = research_counts()
        return {"status": "busy", **counts, "started": 0}
    try:
        return _sync_locked(max(1, min(int(limit), 10)))
    finally:
        _sync_lock.release()


def _sync_locked(limit: int) -> Dict[str, Any]:
    SessionLocal, Event, WebResearchRun, WebEvidence = _models()
    started: List[Dict[str, str]] = []

    with SessionLocal() as session:
        stale = (
            session.query(WebResearchRun)
            .filter(WebResearchRun.status == "running")
            .all()
        )
        for run in stale:
            if str(run.event_id) not in _inflight:
                run.status = "queued"
        session.commit()

        events = (
            session.query(Event)
            .filter(Event.status.in_(("live", "prematch")))
            .filter(Event.source_event_id.op("~")(r"^[0-9]+$"))
            .all()
        )
        for event in events:
            evidence = (
                session.query(WebEvidence.id)
                .filter(WebEvidence.event_id == event.id)
                .first()
            )
            if evidence is not None:
                continue
            existing = (
                session.query(WebResearchRun)
                .filter(WebResearchRun.event_id == event.id)
                .order_by(WebResearchRun.created_at.desc())
                .first()
            )
            if existing is not None:
                continue
            session.add(
                WebResearchRun(
                    id=uuid.uuid4(),
                    event_id=event.id,
                    query=f"{event.participant_a_name} vs {event.participant_b_name}"[:255],
                    status="queued",
                    documents_found=0,
                )
            )
        session.commit()

        running = (
            session.query(WebResearchRun)
            .filter(WebResearchRun.status == "running")
            .count()
        )
        slots = max(0, limit - int(running))
        pending = []
        if slots:
            pending = (
                session.query(WebResearchRun, Event)
                .join(Event, Event.id == WebResearchRun.event_id)
                .filter(WebResearchRun.status == "queued")
                .order_by(WebResearchRun.created_at.asc())
                .limit(slots)
                .all()
            )
            for run, _event in pending:
                run.status = "running"
            session.commit()
            started = [
                {
                    "event_id": str(event.id),
                    "sport_code": event.sport_code or "tennis",
                    "participant_a": event.participant_a_name or "Participant A",
                    "participant_b": event.participant_b_name or "Participant B",
                }
                for _run, event in pending
            ]
            for job in started:
                _inflight.add(job["event_id"])

    for job in started:
        threading.Thread(
            target=_call_research,
            kwargs=job,
            daemon=True,
            name=f"research-{job['event_id'][:8]}",
        ).start()

    counts = research_counts()
    return {"status": "success", "started": len(started), **counts}
