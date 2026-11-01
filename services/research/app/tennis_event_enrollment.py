"""Recover missing recent event jobs in bounded, resumable pages."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


def _run_limit_reached(started: float, limit_seconds: int, *, now: float | None = None) -> bool:
    current = time.monotonic() if now is None else now
    return current - started >= max(1, int(limit_seconds))


def _event_has_supported_fonbet_id(source_event_id: str | None) -> bool:
    """Singles and doubles use the same numeric Fonbet parent ID contract."""
    return str(source_event_id or "").isdigit()


def _doubles_archive_checkpoint_is_current(state, event_count, digest, now):
    """Reuse a completed daily archive pass only while its event cohort is unchanged."""
    if (not state.get("complete") or state.get("source_event_count") != event_count
            or state.get("source_event_digest") != digest):
        return False
    completed_at = datetime.fromisoformat(state["completed_at"])
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    return now - completed_at < timedelta(hours=24)


def reconcile_recent_events(prepare, *, page_size=100, max_runtime_seconds=900):
    from db.connection import SessionLocal, engine
    from db.tennis_enrollment import enroll_tennis_events

    key = "recent_fonbet_event_enrollment_v1"
    started = time.monotonic()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock, SessionLocal() as session:
        # One reconciler across worker restarts/deployments; no locks across
        # network work and the checkpoint advances only after preparation.
        acquired = lock.execute(text(
            "SELECT pg_try_advisory_lock(hashtext('neurobet:tennis-event-enrollment'))"
        )).scalar()
        if not acquired:
            return {"status": "already_running"}
        try:
            state = session.execute(text("""
                SELECT state_json FROM external_tennis_source_state
                WHERE source='neurobet' AND state_key=:key
            """), {"key": key}).scalar() or {}
            if state.get("complete") or not state:
                now = datetime.now(timezone.utc)
                state = {"from": (now - timedelta(days=1 if state.get("seven_day_complete") else 7)).isoformat(),
                         "until": now.isoformat(), "cursor_time": None, "cursor_id": None,
                         "seven_day_complete": bool(state.get("seven_day_complete")), "complete": False}
                session.execute(text("""
                    INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                    VALUES ('neurobet',:key,CAST(:state AS jsonb),now())
                    ON CONFLICT (source,state_key) DO UPDATE SET state_json=excluded.state_json,
                      updated_at=excluded.updated_at
                """), {"key": key, "state": json.dumps(state)})
                session.commit()
            processed = 0
            while True:
                rows = session.execute(text("""
                    SELECT id, first_seen_at, source_event_id, participant_a_name, participant_b_name
                    FROM events WHERE source='fonbet' AND sport_code='tennis'
                      AND first_seen_at >= CAST(:start AS timestamptz)
                      AND first_seen_at <= CAST(:end AS timestamptz)
                      AND (CAST(:cursor_time AS timestamptz) IS NULL OR (first_seen_at, id) >
                          (CAST(:cursor_time AS timestamptz), CAST(:cursor_id AS uuid)))
                    ORDER BY first_seen_at, id LIMIT :page_size
                """), {"start": state["from"], "end": state["until"],
                       "cursor_time": state.get("cursor_time"), "cursor_id": state.get("cursor_id"),
                       "page_size": page_size}).mappings().all()
                ids = [str(row.id) for row in rows]
                if ids:
                    enroll_tennis_events(session, ids)
                    session.commit()
                    supported = [str(row.id) for row in rows
                                 if _event_has_supported_fonbet_id(row.source_event_id)]
                    if supported:
                        asyncio.run(prepare(supported))
                    state.update(cursor_time=rows[-1].first_seen_at.isoformat(), cursor_id=ids[-1])
                    processed += len(ids)
                else:
                    state.update(complete=True, seven_day_complete=True)
                session.execute(text("""
                    INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                    VALUES ('neurobet',:key,CAST(:state AS jsonb),now())
                    ON CONFLICT (source,state_key) DO UPDATE SET state_json=excluded.state_json,
                      updated_at=excluded.updated_at
                """), {"key": key, "state": json.dumps(state)})
                session.commit()
                if not ids:
                    return {"status": "complete", "processed": processed, "window": state["from"]}
                if _run_limit_reached(started, max_runtime_seconds):
                    return {"status": "incomplete", "processed": processed, "window": state["from"],
                            "cursor_time": state.get("cursor_time"), "cursor_id": state.get("cursor_id")}
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(hashtext('neurobet:tennis-event-enrollment'))"))


def reconcile_doubles_archive(prepare, *, page_size=100, max_runtime_seconds=900):
    """Checkpoint a full archive walk to enroll every saved doubles event."""
    from db.connection import SessionLocal, engine
    from db.tennis_enrollment import enroll_tennis_events

    key = "tennis_doubles_archive_roster_enrollment_v1"
    started = time.monotonic()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock, SessionLocal() as session:
        if not lock.execute(text(
            "SELECT pg_try_advisory_lock(hashtext('neurobet:tennis-doubles-archive-enrollment'))"
        )).scalar():
            return {"status": "already_running"}
        try:
            state = session.execute(text("""
                SELECT state_json FROM external_tennis_source_state
                WHERE source='neurobet' AND state_key=:key
            """), {"key": key}).scalar() or {}
            if isinstance(state, str):
                state = json.loads(state)
            if state.get("complete"):
                return {"status": "complete", "processed": int(state.get("processed", 0)),
                        "cursor_id": state.get("cursor_id"),
                        "unsupported_nonnumeric_ids": int(state.get("unsupported_nonnumeric_ids", 0))}
            state.setdefault("cursor_id", None)
            state.setdefault("processed", 0)
            state.setdefault("unsupported_nonnumeric_ids", 0)
            session.execute(text("""
                INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                VALUES ('neurobet',:key,CAST(:state AS jsonb),now())
                ON CONFLICT (source,state_key) DO NOTHING
            """), {"key": key, "state": json.dumps(state)})
            session.commit()
            while True:
                rows = session.execute(text("""
                    SELECT id, source_event_id FROM events
                    WHERE source='fonbet' AND sport_code='tennis'
                      AND (coalesce(participant_a_name,'') LIKE '%/%'
                           OR coalesce(participant_b_name,'') LIKE '%/%')
                      AND (:cursor_id IS NULL OR id::text > :cursor_id)
                    ORDER BY id LIMIT :page_size
                """), {"cursor_id": state.get("cursor_id"), "page_size": max(1, min(100, page_size))}).all()
                ids = [str(row.id) for row in rows]
                if not ids:
                    state["complete"] = True
                else:
                    enroll_tennis_events(session, ids)
                    session.commit()
                    eligible = [str(row.id) for row in rows
                                if str(row.source_event_id or "").isdigit()]
                    state["unsupported_nonnumeric_ids"] += len(rows) - len(eligible)
                    if eligible:
                        asyncio.run(prepare(eligible))
                    state["cursor_id"] = ids[-1]
                    state["processed"] += len(ids)
                session.execute(text("""
                    INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                    VALUES ('neurobet',:key,CAST(:state AS jsonb),now())
                    ON CONFLICT (source,state_key) DO UPDATE SET state_json=excluded.state_json,
                      updated_at=excluded.updated_at
                """), {"key": key, "state": json.dumps(state)})
                session.commit()
                if state.get("complete"):
                    return {"status": "complete", "processed": state["processed"],
                            "cursor_id": state.get("cursor_id"),
                            "unsupported_nonnumeric_ids": state["unsupported_nonnumeric_ids"]}
                if _run_limit_reached(started, max_runtime_seconds):
                    return {"status": "incomplete", "processed": state["processed"],
                            "cursor_id": state.get("cursor_id"),
                            "unsupported_nonnumeric_ids": state["unsupported_nonnumeric_ids"]}
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(hashtext('neurobet:tennis-doubles-archive-enrollment'))"))


def reconcile_doubles_point_archive(schedule, *, page_size=100, max_runtime_seconds=900,
                                    state_key="tennis_doubles_point_archive_v1"):
    """Checkpoint a full finished-doubles PBP queue pass without network work."""
    from db.connection import SessionLocal, engine

    started = time.monotonic()
    page_size = max(1, min(100, int(page_size)))

    def archive_cohort(session):
        row = session.execute(text("""
            SELECT count(*) AS event_count,
                   md5(coalesce(string_agg(
                     concat_ws('|', id::text, source_event_id, participant_a_name,
                               participant_b_name, coalesce(finished_at::text, '')),
                     E'\\n' ORDER BY id), '')) AS digest
            FROM events
            WHERE source='fonbet' AND sport_code='tennis' AND status='finished'
              AND source_event_id ~ '^[0-9]+$'
              AND (coalesce(participant_a_name,'') LIKE '%/%'
                   OR coalesce(participant_b_name,'') LIKE '%/%')
        """)).one()
        return int(row.event_count or 0), str(row.digest or "")

    def reset_state(state, *, generation, event_count, digest):
        return {
            "generation": generation, "cursor_id": None, "processed": 0,
            "queued": 0, "waiting_players": 0, "already_complete": 0,
            "source_event_count": event_count, "source_event_digest": digest,
        }

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
        if not lock.execute(text(
            "SELECT pg_try_advisory_lock(hashtext('neurobet:tennis-doubles-point-archive'))"
        )).scalar():
            return {"status": "already_running"}
        try:
            while True:
                with SessionLocal() as session:
                    state = session.execute(text("""
                        SELECT state_json FROM external_tennis_source_state
                        WHERE source='neurobet' AND state_key=:key
                    """), {"key": state_key}).scalar() or {}
                    if isinstance(state, str):
                        state = json.loads(state)
                    current_count, current_digest = archive_cohort(session)
                    checkpoint_current = _doubles_archive_checkpoint_is_current(
                        state, current_count, current_digest, datetime.now(timezone.utc)
                    )
                    cohort_changed = (
                        state.get("source_event_count") != current_count
                        or state.get("source_event_digest") != current_digest
                    )
                    legacy_partial_checkpoint = bool(state.get("cursor_id")) and not state.get("source_event_digest")
                    if checkpoint_current:
                        return {"status": "complete", **state}
                    if state.get("complete") or cohort_changed or legacy_partial_checkpoint:
                        state = reset_state(
                            state, generation=int(state.get("generation", 0)) + 1,
                            event_count=current_count, digest=current_digest,
                        )
                    state.setdefault("generation", 1)
                    state.setdefault("cursor_id", None)
                    state.setdefault("processed", 0)
                    state.setdefault("queued", 0)
                    state.setdefault("waiting_players", 0)
                    state.setdefault("already_complete", 0)
                    session.execute(text("""
                        INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                        VALUES ('neurobet',:key,CAST(:state AS jsonb),now())
                        ON CONFLICT (source,state_key) DO UPDATE SET state_json=excluded.state_json,
                          updated_at=excluded.updated_at
                    """), {"key": state_key, "state": json.dumps(state)})
                    session.commit()
                    rows = session.execute(text("""
                        SELECT id FROM events
                        WHERE source='fonbet' AND sport_code='tennis' AND status='finished'
                          AND source_event_id ~ '^[0-9]+$'
                          AND (coalesce(participant_a_name,'') LIKE '%/%'
                               OR coalesce(participant_b_name,'') LIKE '%/%')
                          AND (:cursor_id IS NULL OR id::text > :cursor_id)
                        ORDER BY id LIMIT :page_size
                    """), {"cursor_id": state.get("cursor_id"), "page_size": page_size}).all()
                    page_ids = [str(row.id) for row in rows]
                    session.commit()
                if page_ids:
                    counts = schedule(page_ids) or {}
                    state["cursor_id"] = page_ids[-1]
                    state["processed"] += len(page_ids)
                    for key in ("queued", "waiting_players", "already_complete"):
                        state[key] += int(counts.get(key, 0) or 0)
                else:
                    with SessionLocal() as session:
                        final_count, final_digest = archive_cohort(session)
                    if (final_count != state.get("source_event_count")
                            or final_digest != state.get("source_event_digest")):
                        state = reset_state(
                            state, generation=int(state.get("generation", 1)) + 1,
                            event_count=final_count, digest=final_digest,
                        )
                    else:
                        state["complete"] = True
                        state["completed_at"] = datetime.now(timezone.utc).isoformat()
                with SessionLocal() as session:
                    session.execute(text("""
                        INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                        VALUES ('neurobet',:key,CAST(:state AS jsonb),now())
                        ON CONFLICT (source,state_key) DO UPDATE SET state_json=excluded.state_json,
                          updated_at=excluded.updated_at
                    """), {"key": state_key, "state": json.dumps(state)})
                    session.commit()
                if state.get("complete"):
                    return {"status": "complete", **state}
                if _run_limit_reached(started, max_runtime_seconds):
                    return {"status": "incomplete", **state}
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(hashtext('neurobet:tennis-doubles-point-archive'))"))
