"""Persist Fonbet raw snapshots from Redis into sporting events."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List

logger = logging.getLogger("worker.ingest")

SYNTHETIC_PREFIXES = ("sim_evt_", "ext_", "evt_", "dr_")


def is_synthetic_source_id(source_event_id: str) -> bool:
    return source_event_id.startswith(SYNTHETIC_PREFIXES)


def _observed_at(payload: Dict[str, Any], fallback: datetime | None = None) -> datetime:
    """Read the collector timestamp without replacing it with replay time."""
    raw = payload.get("collected_at")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00")) if isinstance(raw, str) else fallback
    except ValueError:
        value = fallback
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def source_payload_provenance(payload: Dict[str, Any]) -> tuple[str | None, str | None]:
    """Return the archived upstream payload digest and MinIO key from a feed envelope."""
    digest = payload.get("source_payload_hash")
    object_key = payload.get("source_payload_s3_key")
    return (digest if isinstance(digest, str) else None,
            object_key if isinstance(object_key, str) else None)


def should_reconcile_live_board(payload: Dict[str, Any], *, current_snapshot: bool,
                                seen_ids: Iterable[str]) -> bool:
    """Only a current complete live-board snapshot can interrupt missing live events."""
    return bool(seen_ids) and current_snapshot and payload.get("page_type", "live") == "live"


def persist_fonbet_snapshot(
    payload: Dict[str, Any], observed_at: datetime | None = None,
    source_payload_hash: str | None = None, source_payload_raw_key: str | None = None,
) -> int:
    """
    Upsert tennis events from a collector snapshot.

    An empty snapshot does not clear live rows: a failed Fonbet poll must not
    wipe the board. When the snapshot contains events, tennis rows missing from
    the feed for >90s leave the live board as ``interrupted`` while waiting for
    an official Fonbet result. Only settlement sync may set ``finished``.
    Synthetic fixtures are ignored. Last known score is preserved.
    """
    events = payload.get("events") or []
    if not isinstance(events, list) or not events:
        logger.info("Fonbet snapshot has no events; leaving current live rows unchanged.")
        return 0

    from db.connection import SessionLocal
    from db.models.events import Event
    from db.tennis_enrollment import enroll_tennis_events
    from tennis_adapter.pipeline import TennisPipeline

    pipeline = TennisPipeline()
    snapshot_at = observed_at or _observed_at(payload)
    current_snapshot = snapshot_at >= datetime.now(timezone.utc) - timedelta(seconds=90)
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
                pipeline.process_and_persist(
                    session, raw, observed_at=snapshot_at,
                    source_payload_hash=source_payload_hash,
                    source_payload_raw_key=source_payload_raw_key,
                )
                seen_ids.append(source_event_id)
                persisted += 1
            except Exception as exc:
                session.rollback()
                logger.exception("Failed to persist event %s: %s", source_event_id, exc)

        if seen_ids:
            try:
                event_ids = [row[0] for row in session.query(Event.id).filter(
                    Event.source == "fonbet", Event.source_event_id.in_(seen_ids)
                ).all()]
                enroll_tennis_events(session, event_ids)
                session.commit()
            except Exception:
                session.rollback()
                # The checkpointed research reconciler repairs a missed batch.
                logger.exception("Failed to enroll tennis snapshot in research queues")

        # Prematch catalog snapshots are a separate event-discovery stream.
        # They must never mark live events missing simply because the catalog
        # and live board contain different cohorts.
        if should_reconcile_live_board(payload, current_snapshot=current_snapshot, seen_ids=seen_ids):
            # Grace window: a single partial/jittered Fonbet poll must not wipe the board.
            # Only take live rows off the board after they have been unseen long enough.
            # Do not mark finished or wipe current_score — that requires official results.
            now = datetime.now(timezone.utc)
            stale_before = now - timedelta(seconds=90)
            awaiting = (
                session.query(Event)
                .filter(
                    Event.sport_code == "tennis",
                    Event.source == "fonbet",
                    Event.is_live.is_(True),
                    Event.status == "live",
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
                        Event.status: "interrupted",
                    },
                    synchronize_session=False,
                )
            )
            session.commit()
            if awaiting:
                logger.info(
                    "Moved %d tennis events to interrupted (awaiting official result); "
                    "missing from live snapshot for >90s.",
                    awaiting,
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
