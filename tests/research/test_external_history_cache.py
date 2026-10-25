from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import asyncio
import uuid
import pytest


def test_player_refresh_completes_from_fresh_external_history_without_flashscore(monkeypatch):
    from db.models import Participant, TennisPlayerIdentityLink, TennisPlayerProfile, TennisPlayerRefreshJob
    from app import main, player_data_coverage, source_metrics

    now = datetime.now(timezone.utc)
    participant_id = "participant-1"
    owner = SimpleNamespace(progress_json={}, status="running", lease_until=now + timedelta(minutes=2))
    profile = SimpleNamespace(
        metrics_version=2,
        history_match_count=27,
        metrics_available_at=now - timedelta(days=2),
        source_identities={"archive:api_tennis": "external-player-7"},
        last_full_history_scan_at=None,
        profile_url=None,
        last_attempt_at=None,
        last_success_at=None,
        sync_status="running",
        sync_error=None,
    )
    participant = SimpleNamespace(canonical_name="Player Example")
    coverage = SimpleNamespace(
        source="api_tennis", checked_at=now - timedelta(days=2), status="partial", complete=False,
    )

    class Query:
        def __init__(self, model):
            self.model = model

        def filter_by(self, **_kwargs):
            return self

        def filter(self, *_args):
            return self

        def with_for_update(self, **_kwargs):
            return self

        def order_by(self, *_args):
            return self

        def first(self):
            if self.model is TennisPlayerRefreshJob:
                return owner
            return None

        def all(self):
            return []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def query(self, model):
            assert model in {TennisPlayerRefreshJob, TennisPlayerIdentityLink}
            return Query(model)

        def get(self, model, _participant_id):
            if model is TennisPlayerProfile:
                return profile
            if model is Participant:
                return participant
            return None

        def commit(self):
            pass

        def get_bind(self):
            return "fake-engine"

    monkeypatch.setattr("db.connection.SessionLocal", Session)
    monkeypatch.setattr(main, "_refresh_job_heartbeat", lambda *_args: None)
    monkeypatch.setattr(main, "_finish_player_refresh_job", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main, "_player_needs_flashscore_identity_for_missing_pbp", lambda *_args: False)
    monkeypatch.setattr(player_data_coverage, "fresh_coverage", lambda *_args, **_kwargs: coverage)
    monkeypatch.setattr(
        player_data_coverage, "fresh_partial_history_coverage", lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(source_metrics, "try_record_source_metric", lambda *_args, **_kwargs: None)

    main._prepare_one_player(participant_id, participant.canonical_name, "attempt-1")

    assert owner.status == "completed"
    assert owner.progress_json["stage"] == "completed_from_external_cache"
    assert owner.progress_json["source"] == "api_tennis"
    assert profile.sync_status == "available"
    assert profile.sync_error is None


def test_player_refresh_keeps_existing_history_and_skips_flashscore_without_identity_or_pbp_demand(monkeypatch):
    from db.models import Participant, TennisPlayerIdentityLink, TennisPlayerProfile, TennisPlayerRefreshJob
    from app import main, player_data_coverage, player_profiles, source_metrics

    now = datetime.now(timezone.utc)
    participant_id = "participant-stale-history"
    owner = SimpleNamespace(
        progress_json={"stage": "starting"}, status="running",
        lease_until=now + timedelta(minutes=2), attempt_token="attempt-1",
    )
    profile = SimpleNamespace(
        metrics_version=2, history_match_count=31,
        metrics_available_at=now - timedelta(days=40),
        source_identities={"archive:api_tennis": "external-player-19"},
        last_full_history_scan_at=None, profile_url=None, last_attempt_at=None,
        last_success_at=None, sync_status="running", sync_error=None,
    )
    participant = SimpleNamespace(canonical_name="Player With Stored History")
    class Query:
        def __init__(self, model): self.model = model
        def filter_by(self, **_kwargs): return self
        def filter(self, *_args): return self
        def with_for_update(self, **_kwargs): return self
        def order_by(self, *_args): return self
        def first(self):
            if self.model is TennisPlayerRefreshJob: return owner
            return None
        def all(self): return []

    class Session:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def query(self, model):
            assert model in {TennisPlayerRefreshJob, TennisPlayerIdentityLink}
            return Query(model)
        def get(self, model, _participant_id):
            if model is TennisPlayerProfile: return profile
            if model is Participant: return participant
            return None
        def commit(self): pass
        def get_bind(self): return "fake-engine"

    monkeypatch.setattr("db.connection.SessionLocal", Session)
    monkeypatch.setattr(main, "_refresh_job_heartbeat", lambda *_args: None)
    monkeypatch.setattr(main, "_finish_player_refresh_job", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main, "_player_needs_flashscore_identity_for_missing_pbp", lambda *_args: False)
    recorded_metrics = []
    monkeypatch.setattr(
        source_metrics, "try_record_source_metric",
        lambda _engine, source, **kwargs: recorded_metrics.append((source, kwargs)),
    )
    monkeypatch.setattr(player_profiles, "seed_profile_from_archive", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(player_data_coverage, "fresh_coverage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(player_data_coverage, "fresh_partial_history_coverage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "_completed_profile_archive_checkpoint", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        main.FlashscoreTennisImporter, "resolve_player_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Flashscore must not be called")),
    )

    main._prepare_one_player(participant_id, participant.canonical_name, "attempt-1")

    assert owner.status == "completed"
    assert owner.progress_json["stage"] == "completed_from_stored_history"
    assert owner.progress_json["flashscore_skipped_reason"] == "STORED_HISTORY_EXISTS"
    assert profile.sync_status == "stale"
    assert profile.sync_error == "STORED_HISTORY_USED_EXTERNAL_REFRESH_PENDING"
    assert recorded_metrics == [("stored_profile_metrics", {"prevented_flashscore": 1})]


def test_player_refresh_rechecks_alternative_history_before_archive_scrape(monkeypatch):
    from db.models import Participant, TennisPlayerIdentityLink, TennisPlayerProfile, TennisPlayerRefreshJob
    from app import main, player_data_coverage, player_profiles, source_metrics

    now = datetime.now(timezone.utc)
    owner = SimpleNamespace(
        progress_json={"stage": "resolving_profile"}, status="running",
        lease_until=now + timedelta(minutes=2), attempt_token="attempt-race",
        last_error=None, completed_at=None,
    )
    profile = SimpleNamespace(
        metrics_version=0, history_match_count=0, metrics_available_at=None,
        source_identities={}, last_full_history_scan_at=None, profile_url=None,
        last_attempt_at=None, last_success_at=None, sync_status="running", sync_error=None,
    )
    participant = SimpleNamespace(canonical_name="Player Example")
    coverage_ready = False
    coverage = SimpleNamespace(source="tennis_my_life", checked_at=now, status="available", complete=True)

    class Query:
        def __init__(self, model): self.model = model
        def filter_by(self, **_kwargs): return self
        def filter(self, *_args): return self
        def with_for_update(self, **_kwargs): return self
        def order_by(self, *_args): return self
        def first(self):
            return owner if self.model is TennisPlayerRefreshJob else None
        def all(self): return []

    class Session:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def query(self, model): return Query(model)
        def get(self, model, _participant_id, **_kwargs):
            return profile if model is TennisPlayerProfile else participant if model is Participant else None
        def add(self, _row): pass
        def commit(self): pass
        def get_bind(self): return "fake-engine"

    def resolve_profile(*_args, **_kwargs):
        nonlocal coverage_ready
        coverage_ready = True
        return {
            "status": "matched", "profile_url": "https://www.flashscore.com/player/example/ExampleId/results/",
            "candidate": {"name": "Player Example", "source_player_id": "ExampleId",
                          "profile_url": "https://www.flashscore.com/player/example/ExampleId/results/"},
        }

    def seed_new_external_history(_session, _participant, target_profile):
        if coverage_ready:
            target_profile.metrics_version = 1
            target_profile.history_match_count = 24
            target_profile.metrics_available_at = now
            return True
        return False

    monkeypatch.setattr("db.connection.SessionLocal", Session)
    monkeypatch.setattr(main, "_refresh_job_heartbeat", lambda *_args: None)
    monkeypatch.setattr(main, "_finish_player_refresh_job", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main, "_player_needs_flashscore_identity_for_missing_pbp", lambda *_args: False)
    monkeypatch.setattr(main.FlashscoreTennisImporter, "resolve_player_profile", resolve_profile)
    monkeypatch.setattr(player_profiles, "seed_profile_from_archive", seed_new_external_history)
    monkeypatch.setattr(
        player_data_coverage, "fresh_coverage",
        lambda *_args, **_kwargs: coverage if coverage_ready else None,
    )
    monkeypatch.setattr(player_data_coverage, "fresh_partial_history_coverage", lambda *_args, **_kwargs: None)
    recorded = []
    monkeypatch.setattr(
        source_metrics, "try_record_source_metric",
        lambda _engine, source, **kwargs: recorded.append((source, kwargs)),
    )
    monkeypatch.setattr(
        main, "_scrape_player_profile_isolated",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("archive scrape must be skipped")),
    )

    main._prepare_one_player("participant-race", participant.canonical_name, "attempt-race")

    assert owner.status == "completed"
    assert owner.attempt_token is None
    assert owner.progress_json["stage"] == "completed_before_archive_from_alternative_history"
    assert owner.progress_json["source"] == "tennis_my_life"
    assert profile.history_match_count == 24
    assert profile.sync_status == "available"
    assert recorded == [("tennis_my_life", {"prevented_flashscore": 1})]


@pytest.mark.parametrize("job_status", ["queued", "retry", "running", "blocked"])
def test_prepare_event_closes_obsolete_flashscore_job_from_fresh_history(monkeypatch, job_status):
    from db.models import Event, Participant, TennisPlayerProfile, TennisPlayerRefreshJob
    from app import main, player_data_coverage

    now = datetime.now(timezone.utc)
    participant = SimpleNamespace(id=uuid.uuid4(), canonical_name="Player Example")
    profile = SimpleNamespace(
        metrics_version=1,
        history_match_count=12,
        metrics_available_at=now - timedelta(days=1),
        source_identities={},
        last_success_at=None,
        last_attempt_at=None,
        sync_status="available",
        sync_error=None,
    )
    event = SimpleNamespace(
        id=uuid.uuid4(), participant_a_id=participant.id, participant_a_name=participant.canonical_name,
        participant_b_id=None, participant_b_name="Doubles A / Doubles B",
    )
    coverage = SimpleNamespace(
        source="tennis_my_life", checked_at=now - timedelta(days=1), status="complete",
        complete=True, source_watermark=None,
    )
    stale_job = SimpleNamespace(
        status=job_status,
        lease_until=now + timedelta(minutes=2) if job_status == "running" else None,
        attempt_token="old-token", completed_at=None,
        progress_json={"stage": "failed_flashscore"}, last_error="SEARCH_COMPLETED_NO_MATCH",
        updated_at=now - timedelta(hours=1),
    )

    class Query:
        def filter(self, *_args):
            return self

        def all(self):
            return [event]

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def query(self, model):
            assert model is Event
            return Query()

        def get(self, model, _key, **_kwargs):
            if model is Participant:
                return participant
            if model is TennisPlayerProfile:
                return profile
            if model is TennisPlayerRefreshJob:
                return stale_job
            return None

        def commit(self):
            pass

        def get_bind(self):
            return "fake-engine"

    monkeypatch.setattr("db.connection.SessionLocal", Session)
    monkeypatch.setattr(player_data_coverage, "fresh_coverage", lambda *_args, **_kwargs: coverage)
    monkeypatch.setattr(player_data_coverage, "fresh_partial_history_coverage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(player_data_coverage, "profile_needs_history_refresh", lambda **_kwargs: False)
    monkeypatch.setattr(main, "_player_needs_flashscore_identity_for_missing_pbp", lambda *_args: False)
    from app import player_profiles
    monkeypatch.setattr(player_profiles, "has_unmaterialized_external_results", lambda *_args: False)
    monkeypatch.setattr(player_data_coverage, "source_capability_enabled", lambda *_args: True)
    from app import source_metrics
    recorded = []
    monkeypatch.setattr(
        source_metrics, "try_record_source_metric",
        lambda engine, source, **kwargs: recorded.append((engine, source, kwargs)),
    )

    result = asyncio.run(main.prepare_tennis_event_players(
        main.TennisPreparationRequest(event_ids=[event.id])
    ))

    assert result["events"][str(event.id)]["ready"] is True
    assert result["events"][str(event.id)]["players"][0]["history_source"] == "tennis_my_life"
    assert stale_job.status == "completed"
    assert stale_job.attempt_token is None
    assert stale_job.progress_json["flashscore_skipped_reason"] == "ALTERNATIVE_HISTORY_AVAILABLE"
    assert recorded == [("fake-engine", "tennis_my_life", {"prevented_flashscore": 1})]


def test_dispatch_cache_reconciliation_can_close_blocked_jobs_without_touching_running_leases():
    from sqlalchemy import true
    from db.models import TennisPlayerProfile, TennisPlayerRefreshJob
    from db.models.external_tennis import TennisPlayerDataCoverage
    from app.main import _external_history_cache_job_filters

    now = datetime.now(timezone.utc)
    status_filter = _external_history_cache_job_filters(
        TennisPlayerRefreshJob, TennisPlayerProfile, TennisPlayerDataCoverage, now, true(),
    )[0]
    compiled = str(status_filter.compile(compile_kwargs={"literal_binds": True}))

    assert "blocked" in compiled
    assert "running" not in compiled


def test_fresh_tennis_explorer_result_feed_is_partial_coverage_only_after_fact_materialization(monkeypatch):
    from app import player_data_coverage, player_profiles

    now = datetime.now(timezone.utc)
    participant_id = uuid.uuid4()
    canonical_player_id = uuid.uuid4()
    match_key = "canonical-match-1"
    checked_at = now - timedelta(hours=1)
    person = SimpleNamespace(
        id=canonical_player_id,
        source_ids={"tennis_explorer": "panshina"},
    )
    profile = SimpleNamespace(
        canonical_player_id=canonical_player_id,
        source_identities={"archive:tennis_explorer": "panshina"},
        metrics_version=3,
        history_match_count=100,
        metrics_available_at=now - timedelta(minutes=30),
    )
    canonical_match = SimpleNamespace(
        canonical_key=match_key,
        winner_player_id=canonical_player_id,
        loser_player_id=uuid.uuid4(),
        played_at=now - timedelta(days=1),
        available_at=checked_at,
        stats={"sources": ["tennis_explorer:te-match-1"]},
    )
    source_match = SimpleNamespace(
        source_match_id="te-match-1",
        winner_player_id="panshina",
        loser_player_id="opponent",
        available_at=checked_at,
    )
    fact = SimpleNamespace(
        source_match_id=match_key,
        available_at=checked_at,
        valid=True,
    )

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class Session:
        def __init__(self, rows):
            self.results = [Result([("tennis_explorer", "panshina")]),
                            Result([("tennis_explorer", "panshina")]),
                            Result(rows[0]), Result(rows[1]), Result(rows[2])]

        def execute(self, _statement):
            return self.results.pop(0)

        def flush(self):
            pass

    saved = []
    monkeypatch.setattr(
        player_data_coverage, "upsert_coverage",
        lambda _session, **coverage: saved.append(coverage),
    )

    session = Session([[canonical_match], [source_match], [fact]])
    result = player_profiles._record_tennis_explorer_recent_results_coverage(
        session, SimpleNamespace(id=participant_id), profile, person, now,
    )

    assert result is True
    assert saved[0]["source"] == "tennis_explorer"
    assert saved[0]["status"] == "partial"
    assert saved[0]["complete"] is False
    assert saved[0]["evidence"]["usable_for_metrics"] is True
    assert saved[0]["evidence"]["projected_matches"] == 1


def test_tennis_explorer_recent_feed_does_not_cover_an_unmaterialized_or_other_player_match():
    from app import player_profiles

    now = datetime.now(timezone.utc)
    person_id = uuid.uuid4()
    profile = SimpleNamespace(
        canonical_player_id=person_id,
        source_identities={"archive:tennis_explorer": "panshina"},
        metrics_version=1, history_match_count=5, metrics_available_at=now,
    )
    person = SimpleNamespace(id=person_id, source_ids={"tennis_explorer": "panshina"})
    match = SimpleNamespace(
        canonical_key="match-1", winner_player_id=person_id, loser_player_id=uuid.uuid4(),
        played_at=now - timedelta(days=1), available_at=now - timedelta(hours=1),
        stats={"sources": ["tennis_explorer:te-match-1"]},
    )

    class Result:
        def __init__(self, rows): self.rows = rows
        def scalars(self): return self
        def all(self): return self.rows

    class Session:
        def __init__(self):
            self.results = [Result([("tennis_explorer", "panshina")]),
                            Result([("tennis_explorer", "panshina")]), Result([match]), Result([])]
        def execute(self, _statement): return self.results.pop(0)
        def flush(self): pass

    assert not player_profiles._record_tennis_explorer_recent_results_coverage(
        Session(), SimpleNamespace(id=uuid.uuid4()), profile, person, now,
    )
