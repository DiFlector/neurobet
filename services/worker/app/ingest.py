"""Persist Fonbet raw snapshots from Redis into sporting events."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

logger = logging.getLogger("worker.ingest")

SYNTHETIC_PREFIXES = ("sim_evt_", "ext_", "evt_", "dr_")


def is_synthetic_source_id(source_event_id: str) -> bool:
    return source_event_id.startswith(SYNTHETIC_PREFIXES)


def persist_fonbet_snapshot(payload: Dict[str, Any]) -> int:
    """
    Upsert tennis events from a collector snapshot.

    An empty snapshot does not finish live rows: a failed Fonbet poll must not
    clear the board. When the snapshot contains events, tennis rows that
    disappeared from the feed are marked finished. Synthetic fixtures are ignored.
    """
    events = payload.get("events") or []
    if not isinstance(events, list) or not events:
        logger.info("Fonbet snapshot has no events; leaving current live rows unchanged.")
        return 0

    from db.connection import SessionLocal
    from db.models.events import Event
    from tennis_adapter.pipeline import TennisPipeline

    pipeline = TennisPipeline()
    seen_ids: List[str] = []
    persisted = 0

    with SessionLocal() as session:
        for raw in events:
            if not isinstance(raw, dict):
                continue
            source_event_id = str(raw.get("source_event_id") or "")
            if not source_event_id or is_synthetic_source_id(source_event_id):
                continue
            sport_code = str(raw.get("sport_code") or payload.get("sport_code") or "tennis").lower()
            if sport_code != "tennis":
                continue
            try:
                pipeline.process_and_persist(session, raw)
                seen_ids.append(source_event_id)
                persisted += 1
            except Exception as exc:
                session.rollback()
                logger.exception("Failed to persist event %s: %s", source_event_id, exc)

        if seen_ids:
            # Grace window: a single partial/jittered Fonbet poll must not wipe the board.
            # Only finish live rows that have not been seen for long enough.
            from datetime import timedelta

            now = datetime.now(timezone.utc)
            stale_before = now - timedelta(seconds=90)
            finished = (
                session.query(Event)
                .filter(
                    Event.sport_code == "tennis",
                    Event.source == "fonbet",
                    Event.is_live.is_(True),
                    ~Event.source_event_id.in_(seen_ids),
                    Event.last_seen_at.isnot(None),
                    Event.last_seen_at < stale_before,
                    ~Event.source_event_id.like("sim_evt_%"),
                    ~Event.source_event_id.like("ext_%"),
                    ~Event.source_event_id.like("evt_%"),
                    ~Event.source_event_id.like("dr_%"),
                )
                .update(
                    {
                        Event.is_live: False,
                        Event.status: "finished",
                        Event.finished_at: now,
                    },
                    synchronize_session=False,
                )
            )
            session.commit()
            if finished:
                logger.info(
                    "Marked %d tennis events finished; missing from live snapshot for >90s.",
                    finished,
                )

    logger.info("Persisted %d tennis events from Fonbet snapshot.", persisted)
    return persisted


def iter_source_ids(events: Iterable[Dict[str, Any]]) -> List[str]:
    """Test helper: ids a snapshot would treat as present on the live board."""
    ids = []
    for raw in events:
        source_event_id = str(raw.get("source_event_id") or "")
        if source_event_id and not is_synthetic_source_id(source_event_id):
            ids.append(source_event_id)
    return ids
