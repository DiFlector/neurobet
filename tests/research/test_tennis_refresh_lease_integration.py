"""Production-PostgreSQL fencing check for player refresh attempts.

Run inside the Research container (the test creates and removes one isolated
participant/job pair and performs no Flashscore requests).
"""
from datetime import datetime, timedelta, timezone
import uuid

from app.main import (
    _claim_source_point_refresh_job,
    _enqueue_source_point_refresh,
    _finish_player_refresh_job,
    _finish_source_point_refresh_job,
    _run_match_lookup,
    _player_refresh_claim_progress,
)
from app.player_profiles import enqueue_player_refresh
from db.connection import SessionLocal
from db.models import (
    Event, ManualConfirmationRequest, Participant, TennisMatchLookupJob,
    TennisPlayerIdentityLink, TennisPlayerRefreshJob, TennisPointDocument,
    TennisPointHistoryVersion, TennisPointMatchLink, TennisPointSourceRefreshJob,
)


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

        # Persist stage progress while the attempt remains running. This lets
        # restart recovery distinguish an interrupted profile lookup from an
        # interrupted history scrape without releasing the lease token.
        assert _finish_player_refresh_job(
            participant_id, current_token, "running", None,
            {"stage": "resolving_profile", "attempt": 1},
        ) is True
        with SessionLocal() as session:
            job = session.get(TennisPlayerRefreshJob, participant_id)
            assert job.status == "running"
            assert job.attempt_token == current_token
            assert job.progress_json == {"stage": "resolving_profile", "attempt": 1}

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


def test_retry_enqueue_and_claim_preserve_archived_profile_pages():
    participant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    checkpoint = {
        "stage": "loading_history",
        "profile_url": "https://www.flashscore.com/player/example/ExampleId/results/",
        "page_checkpoint": {
            "parser_version": "flashscore-tennis-player-v8",
            "pages": [
                {"page_index": 1, "content_hash": "sha256:page-one", "archived_path": "snapshots/page-1"},
                {"page_index": 2, "content_hash": "sha256:page-two", "archived_path": "snapshots/page-2"},
            ],
        },
    }
    try:
        with SessionLocal() as session:
            session.add(Participant(
                id=participant_id,
                sport_code="tennis",
                canonical_name=f"__checkpoint_test_{participant_id}",
                is_team=False,
                metadata_json={"test_fixture": "checkpoint_recovery"},
            ))
            session.flush()
            session.add(TennisPlayerRefreshJob(
                participant_id=participant_id,
                status="retry",
                attempts=2,
                requested_at=now - timedelta(minutes=10),
                next_run_at=now - timedelta(minutes=5),
                progress_json=checkpoint,
                updated_at=now - timedelta(minutes=5),
            ))
            session.commit()

        with SessionLocal() as session:
            job = enqueue_player_refresh(session, participant_id, "checkpoint_recovery_test")
            session.commit()
            assert job.status == "queued"
            assert job.progress_json["page_checkpoint"] == checkpoint["page_checkpoint"]

            job.status = "running"
            job.attempts += 1
            job.progress_json = _player_refresh_claim_progress(job.progress_json, job.attempts)
            session.commit()

        with SessionLocal() as session:
            job = session.get(TennisPlayerRefreshJob, participant_id)
            assert job.status == "running"
            assert job.progress_json["page_checkpoint"] == checkpoint["page_checkpoint"]
            assert job.progress_json["stage"] == "claimed"
            assert job.progress_json["attempt"] == 3
    finally:
        with SessionLocal() as session:
            job = session.get(TennisPlayerRefreshJob, participant_id)
            if job is not None:
                session.delete(job)
            participant = session.get(Participant, participant_id)
            if participant is not None:
                session.delete(participant)
            session.commit()


def test_source_pbp_task_is_independent_and_reclaimed_after_worker_restart():
    source_match_id = f"S{uuid.uuid4().hex[:10]}"
    source_url = (
        "https://www.flashscore.com/match/tennis/player-a-id/player-b-id/"
        f"?mid={source_match_id}"
    )
    old_token = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    try:
        with SessionLocal() as session:
            assert _enqueue_source_point_refresh(session, source_match_id, source_url, now)
            session.commit()
            job = session.get(TennisPointSourceRefreshJob, source_match_id)
            assert job is not None
            assert job.status == "queued"
            assert job.source_url.endswith(f"?mid={source_match_id}")
            assert session.query(TennisPointMatchLink).filter_by(
                source_match_id=source_match_id, fonbet_event_id=None
            ).count() == 0

            # The first worker stops after claiming; its lease then expires.
            job.status = "running"
            job.attempts = 1
            job.attempt_token = old_token
            job.lease_until = now - timedelta(seconds=1)
            session.commit()

            # A replacement worker claims the same source ID with a new fence.
            job = session.get(TennisPointSourceRefreshJob, source_match_id, with_for_update=True)
            reclaimed = _claim_source_point_refresh_job(job, datetime.now(timezone.utc))
            assert reclaimed is not None
            reclaimed_id, reclaimed_url, new_token = reclaimed
            assert reclaimed_id == source_match_id
            assert reclaimed_url == job.source_url
            assert new_token != old_token
            session.commit()

        assert _finish_source_point_refresh_job(
            source_match_id, old_token, "completed", None
        ) is False
        assert _finish_source_point_refresh_job(
            source_match_id, new_token, "completed", None
        ) is True
        with SessionLocal() as session:
            job = session.get(TennisPointSourceRefreshJob, source_match_id)
            assert job.status == "completed"
            assert job.attempts == 2
            assert job.attempt_token is None and job.lease_until is None
    finally:
        with SessionLocal() as session:
            job = session.get(TennisPointSourceRefreshJob, source_match_id)
            if job is not None:
                session.delete(job)
            session.query(TennisPointMatchLink).filter_by(source_match_id=source_match_id).delete()
            session.commit()


def test_source_only_pbp_documents_persist_without_fonbet_identity(monkeypatch):
    import app.point_history as point_history

    source_match_id = f"P{uuid.uuid4().hex[:10]}"
    source_url = (
        "https://www.flashscore.com/match/tennis/player-a-id/player-b-id/"
        f"?mid={source_match_id}"
    )
    retrieved_at = datetime.now(timezone.utc)
    monkeypatch.setattr(point_history.ResearchStorage, "archive_document", lambda *_args, **_kwargs: "archive/pbp.html")
    monkeypatch.setattr(point_history, "fetch_point_documents", lambda _url, source_access=None: ([{
        "set": 1,
        "url": source_url.replace("?mid=", "summary/point-by-point/set-1/?mid="),
        "html": "<html>source point history</html>",
        "retrieved_at": retrieved_at,
        "set_tab_confirmed": True,
        "collection_status": "complete",
    }], "complete"))
    monkeypatch.setattr(point_history, "parse_point_history_html", lambda _html: {
        "events": [{"point_index": 1, "server": "home", "winner": "home"}],
        "quality": {"complete": True, "confirmed_points": 1, "gaps": []},
    })
    try:
        result = point_history.collect_source_point_history(source_match_id, source_url)
        assert result["status"] == "available"
        assert result["fonbet_link_status"] == "unlinked_at_retrieval"
        with SessionLocal() as session:
            document = session.query(TennisPointDocument).filter_by(source_match_id=source_match_id).one()
            version = session.query(TennisPointHistoryVersion).filter_by(source_match_id=source_match_id).one()
            assert document.retrospective is True
            assert document.availability_status == "known"
            assert version.fonbet_event_id is None
            assert version.retrospective is True
            assert version.events_json[0]["winner"] == "home"
    finally:
        with SessionLocal() as session:
            session.query(TennisPointHistoryVersion).filter_by(source_match_id=source_match_id).delete()
            session.query(TennisPointDocument).filter_by(source_match_id=source_match_id).delete()
            session.commit()


def test_ambiguous_live_match_enqueues_source_jobs_without_linking_to_fonbet(monkeypatch):
    import app.point_history as point_history

    event_id = uuid.uuid4()
    participant_a_id, participant_b_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    token = str(uuid.uuid4())
    source_ids = [f"A{uuid.uuid4().hex[:10]}", f"B{uuid.uuid4().hex[:10]}"]
    candidates = [{
        "source_match_id": source_id,
        "url": f"https://www.flashscore.com/match/tennis/a-id/b-id/?mid={source_id}",
    } for source_id in source_ids]
    monkeypatch.setattr(point_history, "_discover_live_match", lambda *_args, **_kwargs: {
        "status": "ambiguous", "candidates": candidates,
    })
    try:
        with SessionLocal() as session:
            session.add_all([
                Participant(id=participant_a_id, sport_code="tennis", canonical_name=f"__ambiguous_a_{event_id}",
                            is_team=False, metadata_json={"test_fixture": "ambiguous_source_dispatch"}),
                Participant(id=participant_b_id, sport_code="tennis", canonical_name=f"__ambiguous_b_{event_id}",
                            is_team=False, metadata_json={"test_fixture": "ambiguous_source_dispatch"}),
            ])
            session.flush()
            event = Event(
                id=event_id, source="fonbet", source_event_id=f"__ambiguous_{event_id}",
                sport_code="tennis", participant_a_id=participant_a_id, participant_a_name="Player A",
                participant_b_id=participant_b_id, participant_b_name="Player B",
                scheduled_start_at=now - timedelta(minutes=10), first_seen_at=now,
                last_seen_at=now, started_at=now - timedelta(minutes=5), status="live", is_live=True,
            )
            session.add(event)
            session.add_all([
                TennisPlayerIdentityLink(participant_id=participant_a_id, source="flashscore",
                    source_player_id="flash-a", status="confirmed", available_at=now),
                TennisPlayerIdentityLink(participant_id=participant_b_id, source="flashscore",
                    source_player_id="flash-b", status="confirmed", available_at=now),
                TennisMatchLookupJob(event_id=event_id, status="running", attempts=1,
                    attempt_token=token, lease_until=now + timedelta(minutes=3), next_run_at=now),
            ])
            session.commit()

        _run_match_lookup(str(event_id), token)

        with SessionLocal() as session:
            lookup = session.get(TennisMatchLookupJob, event_id)
            assert lookup.status == "waiting_confirmation"
            assert lookup.last_error == "MULTIPLE_LIVE_MATCH_CANDIDATES"
            for source_id in source_ids:
                source_job = session.get(TennisPointSourceRefreshJob, source_id)
                assert source_job is not None and source_job.status == "queued"
                link = session.query(TennisPointMatchLink).filter_by(source_match_id=source_id).one()
                assert link.fonbet_event_id is None and link.status == "ambiguous"
                request = session.query(ManualConfirmationRequest).filter_by(
                    kind="event_match", subject_id=str(event_id), candidate_id=source_id,
                    source="flashscore", status="pending",
                ).one()
                assert request.evidence_json["source_match_id"] == source_id
    finally:
        with SessionLocal() as session:
            session.query(ManualConfirmationRequest).filter_by(subject_id=str(event_id)).delete()
            session.query(TennisPointMatchLink).filter(TennisPointMatchLink.source_match_id.in_(source_ids)).delete(
                synchronize_session=False
            )
            session.query(TennisPointSourceRefreshJob).filter(
                TennisPointSourceRefreshJob.source_match_id.in_(source_ids)
            ).delete(synchronize_session=False)
            session.query(TennisMatchLookupJob).filter_by(event_id=event_id).delete()
            session.query(TennisPlayerIdentityLink).filter(
                TennisPlayerIdentityLink.participant_id.in_([participant_a_id, participant_b_id])
            ).delete(synchronize_session=False)
            session.query(Event).filter_by(id=event_id).delete()
            session.query(Participant).filter(Participant.id.in_([participant_a_id, participant_b_id])).delete(
                synchronize_session=False
            )
            session.commit()
