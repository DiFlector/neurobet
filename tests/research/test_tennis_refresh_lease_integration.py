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
    TennisPlayerProfile,
)
from db.models.external_tennis import (
    ExtTennisCanonicalMatch, ExtTennisCanonicalPlayer, ExtTennisMatch,
    ExtTennisMatchObservation,
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

            # Repeated observations coalesce into the existing job and cannot
            # reset a scheduled retry to "now".
            retry_at = now + timedelta(minutes=40)
            job.status = "retry"
            job.next_run_at = retry_at
            job.last_error = "HTTP_200_NO_PUBLISHED_HISTORY"
            session.commit()
            assert _enqueue_source_point_refresh(
                session, source_match_id, source_url, now + timedelta(seconds=1)
            )
            session.commit()
            job = session.get(TennisPointSourceRefreshJob, source_match_id)
            assert job.status == "retry"
            assert job.next_run_at == retry_at
            assert job.last_error == "HTTP_200_NO_PUBLISHED_HISTORY"

            # The first worker stops after claiming; its lease then expires.
            job.status = "running"
            job.attempts = 1
            job.next_run_at = now - timedelta(minutes=1)
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


def test_api_tennis_archive_links_to_finished_fonbet_event_by_canonical_and_provider_ids():
    """Only the validated source match/player IDs may create an oriented link."""
    from app.point_history import _link_api_tennis_archive_for_event

    now = datetime.now(timezone.utc)
    event_id, participant_a_id, participant_b_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    canonical_a_id, canonical_b_id = uuid.uuid4(), uuid.uuid4()
    source_match_id = f"__api_pbp_link_{uuid.uuid4().hex}"
    source_first_id, source_second_id = "source-player-b", "source-player-a"

    with SessionLocal() as session:
        session.add_all([
            Participant(id=participant_a_id, sport_code="tennis", canonical_name="Test Player A",
                        is_team=False, metadata_json={"test_fixture": "api_pbp_link"}),
            Participant(id=participant_b_id, sport_code="tennis", canonical_name="Test Player B",
                        is_team=False, metadata_json={"test_fixture": "api_pbp_link"}),
        ])
        session.flush()
        event = Event(
            id=event_id, source_event_id=f"__api_pbp_link_{event_id}", sport_code="tennis",
            participant_a_id=participant_a_id, participant_a_name="Test Player A",
            participant_b_id=participant_b_id, participant_b_name="Test Player B",
            scheduled_start_at=now - timedelta(days=2), finished_at=now - timedelta(days=1),
            status="finished", is_live=False, current_score={}, metadata_json={},
        )
        player_a = ExtTennisCanonicalPlayer(
            id=canonical_a_id, display_name="Canonical A", gender="w", surname_key="a",
            initial="a", source_ids={},
        )
        player_b = ExtTennisCanonicalPlayer(
            id=canonical_b_id, display_name="Canonical B", gender="w", surname_key="b",
            initial="b", source_ids={},
        )
        session.add_all([event, player_a, player_b])
        session.flush()
        session.add_all([
            TennisPlayerProfile(participant_id=participant_a_id, canonical_player_id=canonical_a_id),
            TennisPlayerProfile(participant_id=participant_b_id, canonical_player_id=canonical_b_id),
            ExtTennisCanonicalMatch(
                id=uuid.uuid4(), canonical_key=f"canonical-{source_match_id}",
                played_at=now - timedelta(days=2), available_at=now - timedelta(hours=1),
                tour_level="wta", tournament="Test Open", surface="hard", best_of=3,
                round_name="final", gender="w", winner_name="Canonical B", loser_name="Canonical A",
                winner_player_id=canonical_b_id, loser_player_id=canonical_a_id,
                score="6-4 6-3", stats={"sources": [f"api_tennis:{source_match_id}"]},
            ),
            ExtTennisMatch(
                source="api_tennis", source_match_id=source_match_id,
                played_at=now - timedelta(days=2), available_at=now - timedelta(hours=1),
                tour_level="wta", tournament="Test Open", gender="w",
                winner_name="Canonical B", loser_name="Canonical A",
                winner_player_id=source_first_id, loser_player_id=source_second_id,
                score="6-4 6-3", stats={},
            ),
            ExtTennisMatchObservation(
                source="api_tennis", source_match_id=source_match_id,
                observation_hash=uuid.uuid4().hex, available_at=now - timedelta(hours=1),
                source_url="https://api-tennis.invalid/test", parser_version="test",
                payload={"event": {"first_player_key": source_first_id,
                                   "second_player_key": source_second_id}},
            ),
        ])
        session.flush()
        document = TennisPointDocument(
            source="api_tennis", source_match_id=source_match_id,
            source_url="https://api-tennis.invalid/test", content_hash=uuid.uuid4().hex,
            parser_version="api_tennis_pbp_v1", retrieved_at=now - timedelta(hours=1),
            parsed_at=now - timedelta(hours=1), available_at=now - timedelta(hours=1),
            availability_status="known", retrospective=True, parse_status="partial",
            quality_json={"complete": False, "gaps": [{"reason": "terminal_point_missing"}]},
        )
        session.add(document)
        session.flush()
        session.add(TennisPointHistoryVersion(
            document_id=document.id, source_match_id=source_match_id, fonbet_event_id=None,
            parser_version="api_tennis_pbp_v1", retrieved_at=now - timedelta(hours=1),
            parsed_at=now - timedelta(hours=1), available_at=now - timedelta(hours=1),
            availability_status="known", retrospective=True,
            events_json=[{"winner": 0, "server": 1, "state_before": ["30", "15"],
                          "state_after": ["40", "15"], "confirmed": True}],
            quality_json={"complete": False, "confirmed_points": 1,
                          "gaps": [{"reason": "terminal_point_missing"}]},
        ))
        session.flush()

        linked = _link_api_tennis_archive_for_event(session, event, as_of=now)
        assert linked is not None and linked["status"] == "partial"
        assert linked["source"] == "api_tennis" and linked["complete"] is False
        version = session.query(TennisPointHistoryVersion).filter_by(
            document_id=document.id, fonbet_event_id=event_id,
            parser_version="api_tennis_pbp_link_v1",
        ).one()
        assert version.available_at >= now
        assert version.events_json[0]["winner"] == 1
        assert version.events_json[0]["server"] == 0
        assert version.events_json[0]["state_before"] == ["15", "30"]
        assert version.quality_json["complete"] is False
        assert version.quality_json["gaps"] == [{"reason": "terminal_point_missing"}]

        repeated = _link_api_tennis_archive_for_event(session, event, as_of=now + timedelta(seconds=1))
        assert repeated is not None and repeated["linked_version_created"] is False
        assert session.query(TennisPointHistoryVersion).filter_by(
            document_id=document.id, fonbet_event_id=event_id,
            parser_version="api_tennis_pbp_link_v1",
        ).count() == 1
        session.rollback()


def test_mcp_charting_links_only_with_unique_canonical_pair_date_tournament_and_names():
    from app.point_history import _link_match_charting_archive_for_event

    now = datetime.now(timezone.utc)
    event_id, participant_a_id, participant_b_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    canonical_a_id, canonical_b_id = uuid.uuid4(), uuid.uuid4()
    source_match_id = f"__mcp_pbp_link_{uuid.uuid4().hex}"
    match_date = now - timedelta(days=2)

    with SessionLocal() as session:
        mcp_observation = ExtTennisMatchObservation(
            source="match_charting_project", source_match_id=source_match_id,
            observation_hash=uuid.uuid4().hex, available_at=now - timedelta(hours=1),
            source_url="https://mcp.invalid/test.csv", parser_version="mcp-test",
            payload={"source": "match_charting_project", "date": match_date.strftime("%Y%m%d"),
                     "gender": "w", "player_1": "Test Player B", "player_2": "Test Player A",
                     "tournament": "Roland Garros Championships", "round": "Final"},
        )
        session.add_all([
            Participant(id=participant_a_id, sport_code="tennis", canonical_name="Test Player A",
                        is_team=False, metadata_json={"test_fixture": "mcp_pbp_link"}),
            Participant(id=participant_b_id, sport_code="tennis", canonical_name="Test Player B",
                        is_team=False, metadata_json={"test_fixture": "mcp_pbp_link"}),
        ])
        session.flush()
        event = Event(
            id=event_id, source_event_id=f"__mcp_pbp_link_{event_id}", sport_code="tennis",
            participant_a_id=participant_a_id, participant_a_name="Test Player A",
            participant_b_id=participant_b_id, participant_b_name="Test Player B",
            scheduled_start_at=match_date, finished_at=now - timedelta(days=1),
            status="finished", is_live=False, current_score={}, metadata_json={},
        )
        session.add_all([
            event,
            ExtTennisCanonicalPlayer(
                id=canonical_a_id, display_name="Test Player A", gender="w", surname_key="a",
                initial="t", source_ids={},
            ),
            ExtTennisCanonicalPlayer(
                id=canonical_b_id, display_name="Test Player B", gender="w", surname_key="b",
                initial="t", source_ids={},
            ),
        ])
        session.flush()
        session.add_all([
            TennisPlayerProfile(participant_id=participant_a_id, canonical_player_id=canonical_a_id),
            TennisPlayerProfile(participant_id=participant_b_id, canonical_player_id=canonical_b_id),
            ExtTennisCanonicalMatch(
                id=uuid.uuid4(), canonical_key=f"canonical-{source_match_id}",
                played_at=match_date, available_at=now - timedelta(hours=2),
                tour_level="wta", tournament="Wimbledon Championships", surface="hard", best_of=3,
                round_name="final", gender="w", winner_name="Test Player A", loser_name="Test Player B",
                winner_player_id=canonical_a_id, loser_player_id=canonical_b_id,
                score="6-4 6-3", stats={"sources": ["tennis_my_life:test-existing-match"]},
            ),
            mcp_observation,
        ])
        session.flush()
        document = TennisPointDocument(
            source="match_charting_project", source_match_id=source_match_id,
            source_url="https://mcp.invalid/test.csv", content_hash=uuid.uuid4().hex,
            parser_version="match_charting_project_csv_v1", retrieved_at=now - timedelta(hours=1),
            parsed_at=now - timedelta(hours=1), available_at=now - timedelta(hours=1),
            availability_status="known", retrospective=True, parse_status="partial",
            quality_json={"complete": False, "gaps": [{"reason": "sampled_tape"}]},
        )
        session.add(document)
        session.flush()
        session.add(TennisPointHistoryVersion(
            document_id=document.id, source_match_id=source_match_id, fonbet_event_id=None,
            parser_version="match_charting_project_csv_v1", retrieved_at=now - timedelta(hours=1),
            parsed_at=now - timedelta(hours=1), available_at=now - timedelta(hours=1),
            availability_status="known", retrospective=True,
            events_json=[{"winner": 0, "server": 1, "state_before": ["30", "15"],
                          "state_after": ["40", "15"], "confirmed": True}],
            quality_json={"complete": False, "confirmed_points": 1,
                          "gaps": [{"reason": "sampled_tape"}]},
        ))
        session.flush()

        assert _link_match_charting_archive_for_event(session, event, as_of=now) is None
        mcp_observation.payload = {**mcp_observation.payload, "tournament": "Wimbledon Championships"}
        session.flush()
        result = _link_match_charting_archive_for_event(session, event, as_of=now)
        assert result is not None and result["source"] == "match_charting_project"
        assert result["status"] == "partial" and result["complete"] is False
        linked = session.query(TennisPointHistoryVersion).filter_by(
            document_id=document.id, fonbet_event_id=event_id,
            parser_version="match_charting_project_pbp_link_v1",
        ).one()
        assert linked.available_at >= now
        assert linked.events_json[0]["winner"] == 1
        assert linked.events_json[0]["server"] == 0
        assert linked.quality_json["gaps"] == [{"reason": "sampled_tape"}]
        repeated = _link_match_charting_archive_for_event(
            session, event, as_of=now + timedelta(seconds=1),
        )
        assert repeated is not None and repeated["linked_version_created"] is False
        assert session.query(TennisPointHistoryVersion).filter_by(
            document_id=document.id, fonbet_event_id=event_id,
            parser_version="match_charting_project_pbp_link_v1",
        ).count() == 1
        session.rollback()


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
def test_canonical_archive_late_serve_enrichment_is_append_only_and_point_in_time():
    """Exercise the canonical materializer in a rollback-only PostgreSQL transaction."""
    from sqlalchemy.orm import Session
    from db.connection import engine
    from db.models import Sport, TennisPlayerProfile, TennisPlayerResultFact
    from db.models.external_tennis import (
        ExtTennisCanonicalMatch, ExtTennisCanonicalPlayer, ExtTennisMatch,
    )
    from app.player_profiles import seed_profile_from_archive

    now = datetime.now(timezone.utc)
    played_at = now - timedelta(days=30)
    result_available_at = now - timedelta(days=20)
    stats_available_at = now - timedelta(hours=1)
    suffix = uuid.uuid4().hex
    participant_id, canonical_player_id, opponent_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    canonical_match_id = uuid.uuid4()
    source_match_id, source_player_id = f"test-match-{suffix}", f"test-player-{suffix}"

    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection, autoflush=False, expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        assert session.get(Sport, "tennis") is not None
        participant = Participant(
            id=participant_id, sport_code="tennis", canonical_name="Test Winner Example",
            is_team=False, metadata_json={"test_fixture": "late_serve_enrichment"},
        )
        person = ExtTennisCanonicalPlayer(
            id=canonical_player_id, display_name="Test Winner Example", gender="m",
            surname_key="example", initial="t", source_ids={"tml": source_player_id},
        )
        profile = TennisPlayerProfile(
            participant_id=participant_id, canonical_player_id=canonical_player_id,
            source_identities={"archive:tml": source_player_id}, metrics_version=0,
            history_match_count=0, metrics_json={}, sync_status="pending",
        )
        canonical_match = ExtTennisCanonicalMatch(
            id=canonical_match_id, canonical_key=f"canonical-{suffix}",
            played_at=played_at, available_at=result_available_at, tour_level="atp",
            tournament="Integration Test", surface="hard", best_of=3, round_name="F",
            gender="m", winner_name="Test Winner Example", loser_name="Test Opponent Example",
            winner_player_id=canonical_player_id, loser_player_id=opponent_id,
            score="6-4 6-3", stats={"sources": [f"tml:{source_match_id}"]},
        )
        source_match = ExtTennisMatch(
            source="tml", source_match_id=source_match_id, played_at=played_at,
            available_at=result_available_at, tour_level="atp", tournament="Integration Test",
            surface="hard", best_of=3, round_name="F", gender="m",
            winner_name="Test Winner Example", loser_name="Test Opponent Example",
            winner_player_id=source_player_id, loser_player_id="test-opponent",
            score="6-4 6-3", stats={},
        )
        session.add(participant)
        session.flush()
        session.add(person)
        session.flush()
        session.add(profile)
        session.add_all([canonical_match, source_match])
        session.flush()

        assert seed_profile_from_archive(session, participant, profile)
        session.flush()
        first_version = session.query(TennisPlayerResultFact).filter_by(
            participant_id=participant_id, source="canonical_archive",
        ).one()
        assert first_version.serve_points is None and first_version.serve_points_won is None
        assert profile.history_match_count == 1
        assert profile.metrics_json["serve"]["unknown_volume_matches"] == 1
        assert profile.metrics_json["serve"]["points"] == 0
        original_available_at = first_version.available_at

        source_match.stats = {
            "w_svpt": 100, "w_1stWon": 42, "w_2ndWon": 19,
            "l_svpt": 88, "l_1stWon": 35, "l_2ndWon": 12,
        }
        source_match.available_at = stats_available_at
        session.flush()
        assert seed_profile_from_archive(session, participant, profile)
        session.flush()
        versions = session.query(TennisPlayerResultFact).filter_by(
            participant_id=participant_id, source="canonical_archive",
        ).order_by(TennisPlayerResultFact.available_at).all()
        assert len(versions) == 2
        assert versions[0].available_at == original_available_at
        assert versions[0].serve_points is None
        assert versions[1].serve_points == 100
        assert versions[1].serve_points_won == 61
        assert versions[1].available_at >= stats_available_at
        assert profile.metrics_json["serve"]["points"] == 100
        assert profile.metrics_json["serve"]["points_won"] == 61
        assert profile.metrics_json["serve"]["unknown_volume_matches"] == 0

        before_enrichment = session.query(TennisPlayerResultFact).filter(
            TennisPlayerResultFact.participant_id == participant_id,
            TennisPlayerResultFact.source == "canonical_archive",
            TennisPlayerResultFact.available_at <= stats_available_at - timedelta(minutes=1),
        ).order_by(TennisPlayerResultFact.available_at.desc()).first()
        assert before_enrichment.id == first_version.id
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def test_api_tennis_serve_statistics_materialize_with_source_provenance_and_pit_time():
    """API-Tennis exact player IDs publish counts without exposing them early."""
    from sqlalchemy.orm import Session
    from db.connection import engine
    from db.models import Sport, TennisPlayerResultFact
    from app.player_profiles import seed_profile_from_archive

    now = datetime.now(timezone.utc)
    played_at, result_at, stats_at = now - timedelta(days=3), now - timedelta(days=2), now - timedelta(hours=2)
    suffix = uuid.uuid4().hex
    participant_id, player_id, opponent_id, match_id = (uuid.uuid4() for _ in range(4))
    source_match_id, source_player_id = f"api-match-{suffix}", f"api-player-{suffix}"
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False,
                       join_transaction_mode="create_savepoint")
    try:
        assert session.get(Sport, "tennis") is not None
        participant = Participant(id=participant_id, sport_code="tennis", canonical_name="Test API Winner",
                                  is_team=False, metadata_json={"test_fixture": "api_tennis_serve_stats"})
        person = ExtTennisCanonicalPlayer(
            id=player_id, display_name="Test API Winner", gender="m", surname_key="winner", initial="t",
            source_ids={"api_tennis": source_player_id},
        )
        profile = TennisPlayerProfile(
            participant_id=participant_id, canonical_player_id=player_id,
            source_identities={"archive:api_tennis": source_player_id}, metrics_version=0,
            history_match_count=0, metrics_json={}, sync_status="pending",
        )
        canonical_match = ExtTennisCanonicalMatch(
            id=match_id, canonical_key=f"api-canonical-{suffix}", played_at=played_at,
            available_at=result_at, tour_level="atp", tournament="Integration Test", surface="hard",
            best_of=3, round_name="F", gender="m", winner_name="Test API Winner",
            loser_name="Test API Opponent", winner_player_id=player_id, loser_player_id=opponent_id,
            score="6-4 6-3", stats={"sources": [f"api_tennis:{source_match_id}"]},
        )
        source_match = ExtTennisMatch(
            source="api_tennis", source_match_id=source_match_id, played_at=played_at,
            available_at=stats_at, tour_level="atp", tournament="Integration Test", surface="unknown",
            best_of=3, round_name="F", gender="m", winner_name="Test API Winner",
            loser_name="Test API Opponent", winner_player_id=source_player_id,
            loser_player_id=f"api-opponent-{suffix}", score="6-4 6-3",
            stats={"statistics": [
                {"stat_period": "match", "stat_name": "Service Points Won", "player_key": source_player_id,
                 "stat_won": "44", "stat_total": "67"},
                {"stat_period": "match", "stat_name": "Service Points Won", "player_key": f"api-opponent-{suffix}",
                 "stat_won": "31", "stat_total": "58"},
            ]},
        )
        session.add(participant)
        session.flush()
        session.add(person)
        session.flush()
        session.add(profile)
        session.add_all([canonical_match, source_match])
        session.flush()

        assert seed_profile_from_archive(session, participant, profile)
        fact = session.query(TennisPlayerResultFact).filter_by(
            participant_id=participant_id, source="canonical_archive",
        ).one()
        assert (fact.serve_points, fact.serve_points_won) == (67, 44)
        assert fact.source_payload["serve_stats_source"] == "api_tennis"
        assert fact.source_payload["serve_stats_source_match_id"] == source_match_id
        assert fact.available_at >= stats_at
        assert profile.metrics_json["serve"]["points"] == 67
        assert profile.metrics_json["serve"]["points_won"] == 44
        assert session.query(TennisPlayerResultFact).filter(
            TennisPlayerResultFact.participant_id == participant_id,
            TennisPlayerResultFact.available_at <= stats_at - timedelta(minutes=1),
        ).count() == 0
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def test_abbreviated_fonbet_name_uses_unique_full_tml_identity_over_initial_alias():
    """A localized initial can use unique TML history despite a duplicate short alias."""
    from sqlalchemy.orm import Session
    from db.connection import engine
    from db.models import Participant, Sport, TennisPlayerProfile, TennisPlayerResultFact
    from db.models.external_tennis import ExtTennisCanonicalMatch, ExtTennisCanonicalPlayer, ExtTennisMatch
    from app.player_profiles import _canonical_player_surname_keys, seed_profile_from_archive
    from app.tennis_flashscore import _player_name_matches

    now = datetime.now(timezone.utc)
    played_at = now - timedelta(days=20)
    available_at = now - timedelta(days=10)
    suffix = uuid.uuid4().hex
    participant_id, tml_player_id, alias_player_id, opponent_id = (uuid.uuid4() for _ in range(4))
    canonical_match_id = uuid.uuid4()
    source_match_id = f"tml-match-{suffix}"

    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False,
                      join_transaction_mode="create_savepoint")
    try:
        assert session.get(Sport, "tennis") is not None
        participant = Participant(id=participant_id, sport_code="tennis", canonical_name="Кретунеску Ч",
                                  is_team=False, metadata_json={"test_fixture": "tml_abbreviated_name"})
        tml_person = ExtTennisCanonicalPlayer(
            id=tml_player_id, display_name="Cezar Cretunesku", gender="m", surname_key="cretunesku",
            initial="c", source_ids={"tml": f"tml-player-{suffix}"},
        )
        short_alias = ExtTennisCanonicalPlayer(
            id=alias_player_id, display_name="C. Cretunesku", gender="unknown", surname_key="cretunesku",
            initial="c", source_ids={"api_tennis": f"api-player-{suffix}"},
        )
        profile = TennisPlayerProfile(participant_id=participant_id, metrics_version=0,
                                      history_match_count=0, metrics_json={}, sync_status="pending")
        canonical_match = ExtTennisCanonicalMatch(
            id=canonical_match_id, canonical_key=f"tml-canonical-{suffix}", played_at=played_at,
            available_at=available_at, tour_level="atp", tournament="Integration Test",
            surface="hard", best_of=3, round_name="R32", gender="m",
                winner_name="Cezar Cretunesku", loser_name="Test Opponent", winner_player_id=tml_player_id,
            loser_player_id=opponent_id, score="6-4 6-3", stats={"sources": [f"tml:{source_match_id}"]},
        )
        source_match = ExtTennisMatch(
            source="tml", source_match_id=source_match_id, played_at=played_at,
            available_at=available_at, tour_level="atp", tournament="Integration Test",
            surface="hard", best_of=3, round_name="R32", gender="m",
            winner_name="Cezar Cretunesku", loser_name="Test Opponent",
            winner_player_id=f"tml-player-{suffix}", loser_player_id=f"opponent-{suffix}",
            score="6-4 6-3", stats={},
        )
        session.add(participant)
        session.flush()
        session.add_all([tml_person, short_alias, profile, canonical_match, source_match])
        session.flush()

        assert "cretunesku" in _canonical_player_surname_keys(participant.canonical_name)
        assert _player_name_matches(participant.canonical_name, tml_person.display_name)
        assert session.query(ExtTennisCanonicalMatch).filter_by(winner_player_id=tml_player_id).count() == 1
        assert seed_profile_from_archive(session, participant, profile)
        assert profile.canonical_player_id == tml_player_id
        assert profile.source_identities["archive:tml"] == f"tml-player-{suffix}"
        assert profile.history_match_count == 1
        fact = session.query(TennisPlayerResultFact).filter_by(
            participant_id=participant_id, source="canonical_archive",
        ).one()
        assert fact.source_match_id == canonical_match.canonical_key
        assert fact.available_at >= available_at
    finally:
        session.close()
        transaction.rollback()
        connection.close()
