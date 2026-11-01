"""Idempotently register Fonbet tennis events with the research queues."""
from sqlalchemy import text
from datetime import datetime, timezone


def enroll_tennis_events(session, event_ids):
    """Insert missing jobs without disturbing existing leases or decisions.

    Call in the caller's transaction. Doubles are enrolled as well; their
    four-player roster is handled by the preparation worker.
    """
    ids = [str(event_id) for event_id in event_ids]
    if not ids:
        return
    params = {"event_ids": ids}
    common = """
        SELECT e.id,
            CASE WHEN e.source_event_id !~ '^[0-9]+$' THEN 'NONNUMERIC_FONBET_ID'
                 WHEN coalesce(e.participant_a_name, '') LIKE '%/%'
                   OR coalesce(e.participant_b_name, '') LIKE '%/%' THEN 'DOUBLES_ROSTER_PENDING'
                 ELSE NULL END AS reason
        FROM events e WHERE e.id = ANY(CAST(:event_ids AS uuid[]))
          AND e.source = 'fonbet' AND e.sport_code = 'tennis'
    """
    inserted = session.execute(text("""
        INSERT INTO tennis_point_refresh_jobs
            (event_id,status,attempts,last_requested_at,next_run_at,last_error,updated_at)
        SELECT id, CASE WHEN reason = 'NONNUMERIC_FONBET_ID' THEN 'skipped' ELSE 'waiting_players' END,
            0, now(), now(), coalesce(reason,'PLAYER_SOURCE_IDS_MISSING'), now()
        FROM (""" + common + """) eligible
        ON CONFLICT (event_id) DO NOTHING
        RETURNING event_id, status, last_error
    """), params).all()
    if inserted:
        from db.models import TennisPointRefreshJobEvent
        observed_at = datetime.now(timezone.utc)
        for row in inserted:
            session.add(TennisPointRefreshJobEvent(event_id=row.event_id, status=row.status,
                last_error=row.last_error, available_at=observed_at))
    session.execute(text("""
        INSERT INTO tennis_match_lookup_jobs
            (event_id,status,attempts,next_run_at,last_error,updated_at)
        SELECT id, CASE WHEN reason = 'NONNUMERIC_FONBET_ID' THEN 'skipped' ELSE 'waiting_players' END,
            0, now(), coalesce(reason,'PLAYER_SOURCE_IDS_MISSING'), now()
        FROM (""" + common + """) eligible
        ON CONFLICT (event_id) DO NOTHING
    """), params)
