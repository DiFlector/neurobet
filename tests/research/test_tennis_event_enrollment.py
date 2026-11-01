from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, literal, select, text

from app.main import _has_live_board_evidence, _next_post_match_retry, _point_job_identity_ready


def test_ready_job_without_error_is_dispatchable():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        for error, expected in ((None, True), ("PLAYER_SOURCE_IDS_MISSING", False), ("timeout", True)):
            predicate = _point_job_identity_ready(SimpleNamespace(last_error=literal(error)))
            assert bool(connection.execute(select(predicate)).scalar_one()) is expected


def test_post_match_retry_skips_missed_slots_and_repeats_daily():
    finish = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert _next_post_match_retry(finish, finish) == finish + timedelta(minutes=1)
    assert _next_post_match_retry(finish, finish + timedelta(minutes=6)) == finish + timedelta(minutes=15)
    assert _next_post_match_retry(finish, finish + timedelta(days=4)) == finish + timedelta(days=5)


def test_event_enrollment_run_limit_is_checked_after_completed_package():
    from app.tennis_event_enrollment import _run_limit_reached
    assert not _run_limit_reached(100.0, 900, now=999.0)
    assert _run_limit_reached(100.0, 900, now=1000.0)
    assert _run_limit_reached(100.0, 0, now=101.0)


def test_reconciler_keeps_doubles_in_the_supported_fonbet_cohort():
    from app.tennis_event_enrollment import _event_has_supported_fonbet_id
    assert _event_has_supported_fonbet_id("123456")
    assert not _event_has_supported_fonbet_id("event_x")


def test_doubles_archive_checkpoint_replays_when_frozen_event_cohort_changes():
    from app.tennis_event_enrollment import _doubles_archive_checkpoint_is_current

    now = datetime(2026, 10, 30, tzinfo=timezone.utc)
    state = {
        "complete": True, "source_event_count": 1200, "source_event_digest": "old",
        "completed_at": (now - timedelta(hours=1)).isoformat(),
    }
    assert _doubles_archive_checkpoint_is_current(state, 1200, "old", now)
    assert not _doubles_archive_checkpoint_is_current(state, 1213, "new", now)
    assert not _doubles_archive_checkpoint_is_current(state, 1200, "changed", now)


def test_point_dispatch_loads_doubles_roster_model_before_source_gate(monkeypatch):
    import asyncio
    from app import main, player_data_coverage
    monkeypatch.setattr(player_data_coverage, "source_capability_enabled", lambda *_args: False)
    result = asyncio.run(main._dispatch_live_point_history(limit=1))
    assert result["status"] == "source_disabled"


def test_player_preparation_locks_refresh_job_before_profile():
    from app.main import _load_locked_player_profile_state
    calls = []
    refresh_job_model = object()
    profile_model = object()

    class Session:
        def get(self, model, _participant_id, **kwargs):
            calls.append((model, kwargs))
            return model

    profile, job = _load_locked_player_profile_state(
        Session(), "participant", refresh_job_model, profile_model
    )
    assert job is refresh_job_model
    assert profile is profile_model
    assert calls == [
        (refresh_job_model, {"with_for_update": True}),
        (profile_model, {}),
    ]


def test_live_match_requires_archived_hash_and_actual_observation_time():
    digest = "a" * 64
    evidence = {"raw_hash": digest, "raw_object_key": "snapshots/research/fixture/" + digest + ".json",
                "observed_at": datetime.now(timezone.utc).isoformat()}
    assert _has_live_board_evidence(evidence)
    assert not _has_live_board_evidence({**evidence, "raw_object_key": None})
    assert not _has_live_board_evidence({**evidence, "observed_at": "invalid"})
    assert not _has_live_board_evidence({**evidence, "raw_hash": "different"})


def test_legacy_url_and_name_orientation_is_not_admitted():
    from tennis_label_audit import _trusted_link_orientation
    assert _trusted_link_orientation({"method": "automatic_exact_confirmed_player_ids",
                                      "external_home_is_fonbet_a": True}) is None
    assert _trusted_link_orientation({"side_orientation_source": "flashscore_match_observation_names",
                                      "external_home_is_fonbet_a": False}) is None
    assert _trusted_link_orientation({"method": "automatic_exact_confirmed_player_ids",
        "side_orientation_source": "flashscore_scoreboard_dom_confirmed_profile_ids",
        "raw_hash": "a" * 64, "raw_object_key": "snapshots/research/fixture",
        "external_home_is_fonbet_a": False}) is False


def test_confirming_second_player_wakes_match_search(monkeypatch):
    from app import main
    from db.models import TennisMatchLookupJob, TennisPointRefreshJob
    now = datetime.now(timezone.utc)
    event = SimpleNamespace(id="event", participant_a_id="a", participant_b_id="b",
                            is_live=True, finished_at=None, last_seen_at=now)
    point = SimpleNamespace(status="waiting_players", last_error="PLAYER_SOURCE_IDS_MISSING",
                            next_run_at=now, updated_at=now)
    lookup = SimpleNamespace(status="waiting_players", last_error="PLAYER_SOURCE_IDS_MISSING",
                             next_run_at=now, updated_at=now)
    class Query:
        def filter(self, *_args): return self
        def all(self): return [event]
    class Session:
        def flush(self): pass
        def query(self, *_args): return Query()
        def get(self, model, *_args, **_kwargs):
            return point if model is TennisPointRefreshJob else lookup if model is TennisMatchLookupJob else None
    ids = {"a": "source-a"}
    monkeypatch.setattr(main, "_confirmed_flashscore_player_id", lambda _s, pid: ids.get(pid))
    monkeypatch.setattr(main, "_record_point_job_event", lambda *_args: None)
    main._wake_identity_dependent_matches(Session(), "a", now)
    assert point.status == lookup.status == "waiting_players"
    ids["b"] = "source-b"
    main._wake_identity_dependent_matches(Session(), "b", now)
    assert point.status == "waiting_match"
    assert lookup.status == "queued" and lookup.last_error is None


def test_enrollment_is_idempotent_and_queues_doubles_roster_resolution():
    """Exercise PostgreSQL conflict handling in session-local temporary tables."""
    from db.connection import SessionLocal
    from db.tennis_enrollment import enroll_tennis_events
    ids = ["00000000-0000-0000-0000-000000000001",
           "00000000-0000-0000-0000-000000000002",
           "00000000-0000-0000-0000-000000000003"]
    with SessionLocal() as session:
        try:
            session.execute(text("""CREATE TEMP TABLE events (
                id uuid PRIMARY KEY, source text, sport_code text, source_event_id text,
                participant_a_name text, participant_b_name text) ON COMMIT DROP"""))
            session.execute(text("""CREATE TEMP TABLE tennis_point_refresh_jobs (
                event_id uuid PRIMARY KEY, status text, attempts int, last_requested_at timestamptz,
                next_run_at timestamptz, last_error text, updated_at timestamptz) ON COMMIT DROP"""))
            session.execute(text("""CREATE TEMP TABLE tennis_match_lookup_jobs (
                event_id uuid PRIMARY KEY, status text, attempts int, next_run_at timestamptz,
                last_error text, updated_at timestamptz) ON COMMIT DROP"""))
            session.execute(text("""INSERT INTO events VALUES
                (CAST(:a AS uuid),'fonbet','tennis','123','A','B'),
                (CAST(:b AS uuid),'fonbet','tennis','124','A/B','C'),
                (CAST(:c AS uuid),'fonbet','tennis','X','A','B')"""),
                dict(zip(("a", "b", "c"), ids)))
            enroll_tennis_events(session, ids)
            enroll_tennis_events(session, ids)
            rows = session.execute(text("SELECT status,last_error FROM tennis_point_refresh_jobs ORDER BY event_id")).all()
            assert rows == [("waiting_players", "PLAYER_SOURCE_IDS_MISSING"),
                            ("waiting_players", "DOUBLES_ROSTER_PENDING"),
                            ("skipped", "NONNUMERIC_FONBET_ID")]
            assert session.execute(text("SELECT count(*) FROM tennis_match_lookup_jobs")).scalar_one() == 3
        finally:
            session.rollback()
