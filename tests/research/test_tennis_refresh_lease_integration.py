"""Production-PostgreSQL fencing check for player refresh attempts.

Run inside the Research container (the test creates and removes one isolated
participant/job pair and performs no Flashscore requests).
"""
from datetime import datetime, timedelta, timezone
import uuid

from app.main import _finish_player_refresh_job
from db.connection import SessionLocal
from db.models import Participant, TennisPlayerRefreshJob


def test_expired_refresh_attempt_cannot_finish_after_a_new_attempt_owns_job():
    participant_id = uuid.uuid4()
    old_token, current_token = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        session.add(Participant(
            id=participant_id,
            sport_code="tennis",
            canonical_name=f"__lease_test_{participant_id}",
            is_team=False,
            metadata_json={"test_fixture": "lease_fencing"},
        ))
        # SessionLocal deliberately disables implicit autoflush. Ensure the FK
        # row exists before inserting the refresh job in the same transaction.
        session.flush()
        session.add(TennisPlayerRefreshJob(
            participant_id=participant_id,
            status="running",
            attempts=1,
            attempt_token=old_token,
            lease_until=now - timedelta(seconds=1),
            requested_at=now - timedelta(minutes=1),
            next_run_at=now - timedelta(minutes=1),
            progress_json={"stage": "expired_test_attempt"},
        ))
        session.commit()

    try:
        # An expired worker cannot finish even before a replacement claims it.
        assert _finish_player_refresh_job(
            participant_id, old_token, "completed", None, {"stage": "stale"}
        ) is False

        # Simulate the queue reclaim after restart: a new fencing token owns a
        # fresh lease. The former process must not mutate or close that attempt.
        with SessionLocal() as session:
            job = session.get(TennisPlayerRefreshJob, participant_id)
            job.status = "running"
            job.attempt_token = current_token
            job.lease_until = datetime.now(timezone.utc) + timedelta(minutes=5)
            job.progress_json = {"stage": "claimed_after_restart"}
            session.commit()

        assert _finish_player_refresh_job(
            participant_id, old_token, "completed", None, {"stage": "stale"}
        ) is False
        with SessionLocal() as session:
            job = session.get(TennisPlayerRefreshJob, participant_id)
            assert job.status == "running"
            assert job.attempt_token == current_token
            assert job.progress_json == {"stage": "claimed_after_restart"}

        assert _finish_player_refresh_job(
            participant_id, current_token, "completed", None, {"stage": "completed"}
        ) is True
        with SessionLocal() as session:
            job = session.get(TennisPlayerRefreshJob, participant_id)
            assert job.status == "completed"
            assert job.attempt_token is None and job.lease_until is None
    finally:
        with SessionLocal() as session:
            job = session.get(TennisPlayerRefreshJob, participant_id)
            if job is not None:
                session.delete(job)
            participant = session.get(Participant, participant_id)
            if participant is not None:
                session.delete(participant)
            session.commit()
