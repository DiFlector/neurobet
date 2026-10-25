from __future__ import annotations

import asyncio
import inspect
import threading

from app import flashscore_worker


def test_archive_failures_park_until_manual_retry_or_back_off_without_hot_loop():
    from app.main import _flashscore_import_failure_schedule
    from datetime import datetime, timedelta, timezone

    assert _flashscore_import_failure_schedule("CAPTCHA_CHALLENGE_ABORTED", 104) == ("blocked", None)
    assert _flashscore_import_failure_schedule("PLAYER_COUNTRY_CODE_UNRESOLVED", 100) == ("blocked", None)
    assert _flashscore_import_failure_schedule("PROFILE_TIMEOUT_PROCESS_TERMINATED", 100) == ("queued", 3600)
    assert _flashscore_import_failure_schedule(
        "PROFILE_HTTP_FETCH_FAILED:ConnectError:[Errno 111] Connection refused", 2
    ) == ("queued", 86400)
    assert _flashscore_import_failure_schedule(
        "PROFILE_HTTP_FETCH_FAILED:ConnectError:[Errno 111] Connection refused", 3
    ) == ("blocked", None)
    assert _flashscore_import_failure_schedule(
        "PROFILE_HTTP_FETCH_FAILED:ConnectError:[Errno 111] Connection refused", 44
    ) == ("blocked", None)
    assert _flashscore_import_failure_schedule("PAGINATION_NOT_CONFIRMED", 4) == ("queued", 86400)
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    assert _flashscore_import_failure_schedule(
        "PERSISTENT_TRANSPORT_FAILURE", 5,
        progress={"first_failure_at": (now - timedelta(hours=23, minutes=59)).isoformat()}, now=now,
    ) == ("queued", 960)
    assert _flashscore_import_failure_schedule(
        "PERSISTENT_TRANSPORT_FAILURE", 5,
        progress={"first_failure_at": (now - timedelta(hours=24)).isoformat()}, now=now,
    ) == ("blocked", None)
    assert _flashscore_import_failure_schedule(
        "PERSISTENT_TRANSPORT_FAILURE", 5, created_at=now - timedelta(days=2), now=now,
    ) == ("blocked", None)


def test_legacy_persistent_transport_failures_are_parked_after_one_day():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from app.main import _park_stale_persistent_transport_imports

    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    stale = SimpleNamespace(
        status="queued", last_error="PERSISTENT_TRANSPORT_FAILURE", attempts=7,
        progress_json={"first_failure_at": (now - timedelta(days=1)).isoformat(), "checkpoint": 12},
        created_at=now - timedelta(days=2), attempt_token="old", lease_until=now,
        attempt_started_at=now, updated_at=None,
    )
    recent = SimpleNamespace(
        status="retry", last_error="PERSISTENT_TRANSPORT_FAILURE", attempts=4,
        progress_json={"first_failure_at": (now - timedelta(hours=2)).isoformat()},
        created_at=now - timedelta(days=3), attempt_token=None, lease_until=None,
        attempt_started_at=None, updated_at=None,
    )

    class Query:
        def filter(self, *_conditions):
            return self

        def with_for_update(self, **_kwargs):
            return self

        def all(self):
            return [stale, recent]

    class Session:
        def query(self, _model):
            return Query()

    assert _park_stale_persistent_transport_imports(Session(), now) == 1
    assert stale.status == "blocked"
    assert stale.last_error == "PERSISTENT_TRANSPORT_FAILURE"
    assert stale.progress_json["stage"] == "waiting_for_transport_recovery"
    assert stale.progress_json["checkpoint"] == 12
    assert stale.attempt_token is None and stale.lease_until is None
    assert recent.status == "retry"


def test_persistent_transport_failure_age_is_preserved_until_error_changes():
    from datetime import datetime, timezone
    from app.main import _flashscore_failure_progress

    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    earlier = datetime(2026, 10, 22, tzinfo=timezone.utc).isoformat()
    assert _flashscore_failure_progress(
        {"first_failure_at": earlier}, "PERSISTENT_TRANSPORT_FAILURE",
        "PERSISTENT_TRANSPORT_FAILURE", now,
    )["first_failure_at"] == earlier
    assert _flashscore_failure_progress(
        {}, "OTHER_ERROR", "PERSISTENT_TRANSPORT_FAILURE", now,
    )["first_failure_at"] == now.isoformat()
    assert "first_failure_at" not in _flashscore_failure_progress(
        {"first_failure_at": earlier}, "PERSISTENT_TRANSPORT_FAILURE", "RECOVERED", now,
    )


def test_live_match_not_found_uses_exponential_retry_and_honors_due_time():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from app.main import _live_match_lookup_retry_delay, _match_lookup_retry_is_due

    assert _live_match_lookup_retry_delay("LIVE_MATCH_NOT_FOUND", 1) == timedelta(minutes=10)
    assert _live_match_lookup_retry_delay("LIVE_MATCH_NOT_FOUND", 4) == timedelta(minutes=80)
    assert _live_match_lookup_retry_delay("HTTP_429_BLOCKED", 1) == timedelta(minutes=15)
    assert _live_match_lookup_retry_delay("LIVE_MATCH_NOT_FOUND", 100) == timedelta(hours=6)

    now = datetime(2026, 10, 24, 9, tzinfo=timezone.utc)
    future = SimpleNamespace(status="retry", next_run_at=now + timedelta(minutes=2))
    due = SimpleNamespace(status="retry", next_run_at=now)
    awaiting_players = SimpleNamespace(status="waiting_players", next_run_at=now + timedelta(hours=1))
    assert not _match_lookup_retry_is_due(future, now)
    assert _match_lookup_retry_is_due(due, now)
    assert not _match_lookup_retry_is_due(awaiting_players, now)


def test_player_captcha_is_parked_but_transient_profile_errors_still_retry():
    from app.main import _flashscore_profile_job_outcome, _player_refresh_retry_delay

    status, progress = _flashscore_profile_job_outcome(
        "retry", "CAPTCHA_CHALLENGE_ABORTED", {"stage": "source_retry", "page_checkpoint": {"pages": [1]}}
    )
    assert status == "blocked"
    assert progress["stage"] == "waiting_for_captcha_cooldown"
    assert progress["reason"] == "CAPTCHA_CHALLENGE_ABORTED"
    assert progress["page_checkpoint"] == {"pages": [1]}
    status, progress = _flashscore_profile_job_outcome(
        "retry", "PROFILE_TIMEOUT_PROCESS_TERMINATED", {"stage": "source_retry"}
    )
    assert status == "retry"
    assert progress["stage"] == "source_retry"
    for reason in ("PLAYER_COUNTRY_CODE_UNRESOLVED", "SEARCH_CANDIDATES_REJECTED_BY_NAME"):
        status, progress = _flashscore_profile_job_outcome("retry", reason, {})
        assert status == "blocked"
        assert progress["stage"] == "manual_retry_required"
    assert _player_refresh_retry_delay("SEARCH_COMPLETED_NO_MATCH", 30).days == 7
    assert _player_refresh_retry_delay("HTTP_429_BLOCKED", 30).total_seconds() == 900


def test_captcha_lookup_retry_waits_for_source_cooldown(monkeypatch):
    from app.main import _live_match_lookup_retry_delay, _source_point_refresh_retry_delay

    monkeypatch.setenv("FLASHSCORE_CAPTCHA_COOLDOWN_HOURS", "6")
    assert _live_match_lookup_retry_delay("CAPTCHA_CHALLENGE_ABORTED", 1).total_seconds() == 6 * 3600
    assert _source_point_refresh_retry_delay("CAPTCHA_CHALLENGE_ABORTED", 1).total_seconds() == 6 * 3600


def test_expired_captcha_jobs_resume_without_dropping_archive_checkpoint():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from app.main import (
        _resume_expired_captcha_imports,
        _resume_expired_captcha_player_jobs,
    )
    from db.models import FlashscoreImportJob, TennisPlayerRefreshJob

    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    progress = {
        "stage": "waiting_for_captcha_cooldown",
        "captcha_retry_at": (now - timedelta(seconds=1)).isoformat(),
        "page_checkpoints": {"0": {"pages": [{"page_index": 4, "archived_path": "snapshots/page"}]}},
    }
    player = SimpleNamespace(
        status="blocked", last_error="CAPTCHA_CHALLENGE_ABORTED", next_run_at=now,
        progress_json={"captcha_retry_at": progress["captcha_retry_at"]},
        completed_at=now, updated_at=now,
    )
    archive = SimpleNamespace(
        status="blocked", last_error="CAPTCHA_CHALLENGE_ABORTED", next_run_at=now,
        progress_json=progress, attempt_token="old", lease_until=now,
        attempt_started_at=now, updated_at=now,
    )

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *_args):
            return self

        def with_for_update(self, **_kwargs):
            return self

        def all(self):
            return self.rows

    class Session:
        def execute(self, _statement):
            return SimpleNamespace(scalar=lambda: now - timedelta(seconds=1))

        def query(self, model):
            return Query([player] if model is TennisPlayerRefreshJob else [archive])

    session = Session()
    assert _resume_expired_captcha_player_jobs(session, now) == 1
    assert player.status == "queued" and player.last_error is None
    assert player.progress_json["previous_error"] == "CAPTCHA_CHALLENGE_ABORTED"
    assert _resume_expired_captcha_imports(session, now) == 1
    assert archive.status == "queued" and archive.last_error is None
    assert archive.attempt_token is None and archive.lease_until is None
    assert archive.progress_json["page_checkpoints"] == progress["page_checkpoints"]


def test_flashscore_network_slot_fails_fast_while_captcha_circuit_is_open(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from contextlib import contextmanager
    import pytest
    from app import main

    class Connection:
        def execute(self, _statement):
            return type("Result", (), {
                "scalar": staticmethod(lambda: datetime.now(timezone.utc) + timedelta(hours=5))
            })()

    class Engine:
        @contextmanager
        def connect(self):
            yield Connection()

    monkeypatch.setattr(main, "_flashscore_lock_engine", Engine())
    with pytest.raises(TimeoutError, match="FLASHSCORE_SOURCE_COOLDOWN"):
        with main._flashscore_source_access("live"):
            pytest.fail("A request must not acquire a Flashscore slot during the cooldown")


def test_legacy_identity_failure_retries_are_parked_without_losing_attempts():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from app.main import _park_terminal_identity_refresh_jobs

    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    job = SimpleNamespace(
        status="retry", last_error="SEARCH_CANDIDATES_REJECTED_BY_NAME",
        attempts=17, progress_json={"stage": "candidate_name_mismatch"},
        attempt_token="old-token", lease_until=now, attempt_started_at=now,
        updated_at=None,
    )

    class Query:
        def filter(self, *_conditions):
            return self

        def with_for_update(self, **_kwargs):
            return self

        def all(self):
            return [job]

    class Session:
        def query(self, _model):
            return Query()

    assert _park_terminal_identity_refresh_jobs(Session(), now) == 1
    assert job.status == "blocked"
    assert job.attempts == 17
    assert job.last_error == "SEARCH_CANDIDATES_REJECTED_BY_NAME"
    assert job.attempt_token is None and job.lease_until is None
    assert job.progress_json["stage"] == "waiting_for_identity_data"


def test_legacy_pbp_identity_retries_become_dependency_waits():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from app.main import _park_point_jobs_waiting_for_player_ids

    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    job = SimpleNamespace(
        status="retry", last_error="PLAYER_SOURCE_IDS_MISSING", attempts=5,
        attempt_token="old-token", lease_until=now, attempt_started_at=now,
        next_run_at=now, updated_at=None,
    )

    class Query:
        def filter(self, *_conditions):
            return self

        def with_for_update(self, **_kwargs):
            return self

        def all(self):
            return [job]

    class Session:
        def query(self, _model):
            return Query()

    assert _park_point_jobs_waiting_for_player_ids(Session(), now) == 1
    assert job.status == "waiting_players"
    assert job.last_error == "PLAYER_SOURCE_IDS_MISSING"
    assert job.attempts == 5
    assert job.attempt_token is None and job.lease_until is None
    assert job.next_run_at > now


def test_routine_event_enqueue_does_not_defeat_profile_negative_result_backoff():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from app.player_profiles import _preserve_player_refresh_backoff

    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    assert _preserve_player_refresh_backoff(SimpleNamespace(
        status="retry", last_error="SEARCH_COMPLETED_NO_MATCH", next_run_at=now + timedelta(days=6)
    ), "new_event", now)
    assert not _preserve_player_refresh_backoff(SimpleNamespace(
        status="retry", last_error="SEARCH_COMPLETED_NO_MATCH", next_run_at=now + timedelta(days=6)
    ), "automatic_exact_identity_match", now)
    assert _preserve_player_refresh_backoff(SimpleNamespace(
        status="blocked", last_error="SEARCH_CANDIDATES_REJECTED_BY_NAME", next_run_at=now
    ), "new_event", now)
    assert not _preserve_player_refresh_backoff(SimpleNamespace(
        status="blocked", last_error="SEARCH_CANDIDATES_REJECTED_BY_NAME", next_run_at=now
    ), "manual_identity_resolution", now)


def test_source_pbp_retries_back_off_and_cap_at_one_hour():
    from app.main import _source_point_refresh_retry_delay

    assert _source_point_refresh_retry_delay("SOURCE_QUEUE_TIMEOUT", 1).total_seconds() == 300
    assert _source_point_refresh_retry_delay("HTTP_200_NO_PUBLISHED_HISTORY", 2).total_seconds() == 600
    assert _source_point_refresh_retry_delay("HTTP_200_NO_PUBLISHED_HISTORY", 5).total_seconds() == 3600
    assert _source_point_refresh_retry_delay("HTTP_403_BLOCKED", 1).total_seconds() == 900
    assert _source_point_refresh_retry_delay("HTTP_403_BLOCKED", 5).total_seconds() == 3600


def test_executor_heartbeat_runs_outside_dispatch_loop(monkeypatch):
    calls = []
    written = threading.Event()

    def record_heartbeat():
        calls.append(True)
        written.set()

    monkeypatch.setattr(flashscore_worker, "_write_executor_heartbeat", record_heartbeat)
    monkeypatch.setattr(flashscore_worker, "_write_health_file", lambda: None)
    monkeypatch.setattr(flashscore_worker, "HEARTBEAT_STOP", threading.Event())

    thread = threading.Thread(target=flashscore_worker._heartbeat_loop, daemon=True)
    thread.start()
    try:
        assert written.wait(timeout=2)
        assert calls == [True]
    finally:
        flashscore_worker.HEARTBEAT_STOP.set()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_browser_process_budget_defaults_to_two_and_is_bounded(monkeypatch):
    monkeypatch.delenv("FLASHSCORE_BROWSER_PROCESSES", raising=False)
    assert flashscore_worker._browser_process_limit() == 2
    monkeypatch.setenv("FLASHSCORE_BROWSER_PROCESSES", "9")
    assert flashscore_worker._browser_process_limit() == 4
    monkeypatch.setenv("FLASHSCORE_BROWSER_PROCESSES", "0")
    assert flashscore_worker._browser_process_limit() == 1


def test_three_browser_budget_has_independent_current_and_backlog_lanes(monkeypatch):
    monkeypatch.setenv("FLASHSCORE_TENNIS_POINT_CONCURRENCY", "1")
    current, backlog, point, control = flashscore_worker._create_worker_executors(3)
    assert current is not backlog
    assert current._max_workers == backlog._max_workers == point._max_workers == 1
    current_started = threading.Event()
    backlog_started = threading.Event()
    point_started = threading.Event()
    release = threading.Event()
    current_job = current.submit(lambda: (current_started.set(), release.wait(timeout=3)))
    backlog_job = backlog.submit(lambda: (backlog_started.set(), release.wait(timeout=3)))
    point_job = point.submit(lambda: (point_started.set(), release.wait(timeout=3)))
    control_ran = threading.Event()
    try:
        assert current_started.wait(timeout=1)
        assert backlog_started.wait(timeout=1)
        assert point_started.wait(timeout=1)
        assert control.submit(control_ran.set).result(timeout=1) is None
        assert control_ran.is_set()
        assert not current_job.done()
        assert not backlog_job.done()
        assert not point_job.done()
    finally:
        release.set()
        current_job.result(timeout=1)
        backlog_job.result(timeout=1)
        point_job.result(timeout=1)
        current.shutdown(wait=True)
        backlog.shutdown(wait=True)
        point.shutdown(wait=True)
        control.shutdown(wait=True)


def test_two_browser_budget_reuses_one_background_lane():
    current, backlog, point, control = flashscore_worker._create_worker_executors(2)
    assert current is not backlog
    assert current._max_workers == point._max_workers == 1
    assert backlog._max_workers == 1
    current.shutdown(wait=True)
    point.shutdown(wait=True)
    control.shutdown(wait=True)


def test_point_executor_expands_with_browser_budget(monkeypatch):
    monkeypatch.setenv("FLASHSCORE_TENNIS_POINT_CONCURRENCY", "2")
    current, backlog, point, control = flashscore_worker._create_worker_executors(4)
    assert current._max_workers == backlog._max_workers == 1
    assert point._max_workers == 2
    first_started = threading.Event()
    second_started = threading.Event()
    release = threading.Event()
    first = point.submit(lambda: (first_started.set(), release.wait(timeout=3)))
    second = point.submit(lambda: (second_started.set(), release.wait(timeout=3)))
    try:
        assert first_started.wait(timeout=1)
        assert second_started.wait(timeout=1)
    finally:
        release.set()
        first.result(timeout=1)
        second.result(timeout=1)
        current.shutdown(wait=True)
        backlog.shutdown(wait=True)
        point.shutdown(wait=True)
        control.shutdown(wait=True)


def test_http_dispatch_does_not_expose_internal_executor_parameter():
    assert "executor" not in inspect.signature(
        flashscore_worker.research.dispatch_live_point_history
    ).parameters


def test_current_lane_dispatches_ready_match_before_profile(monkeypatch):
    calls = []

    async def matches(**kwargs):
        calls.append(("matches", kwargs))
        return {"active_count": 1, "claimed_count": 1}

    async def players(**kwargs):
        calls.append(("players", kwargs))
        return {"active_count": 0, "claimed_count": 0}

    monkeypatch.setattr(flashscore_worker.research, "dispatch_tennis_player_refresh_jobs", players)
    monkeypatch.setattr(flashscore_worker.research, "dispatch_tennis_match_search", matches)
    current, _, point, control = flashscore_worker._create_worker_executors(2)
    try:
        players_result, match_result = asyncio.run(
            flashscore_worker._dispatch_current_work(current)
        )
        assert players_result == {}
        assert match_result["active_count"] == 1
        assert calls == [("matches", {"limit": 1})]
    finally:
        current.shutdown(wait=True)
        point.shutdown(wait=True)
        control.shutdown(wait=True)


def test_current_and_background_profile_scopes_stay_separate(monkeypatch):
    from app.flashscore_executor_context import profile_scope
    scopes = []

    async def matches(**_kwargs):
        return {"active_count": 0, "claimed_count": 0}

    async def players(**_kwargs):
        scopes.append(profile_scope.get())
        return {"active_count": 0, "claimed_count": 0}

    monkeypatch.setattr(flashscore_worker.research, "dispatch_tennis_match_search", matches)
    monkeypatch.setattr(flashscore_worker.research, "dispatch_tennis_player_refresh_jobs", players)
    monkeypatch.setattr(flashscore_worker, "_schedule_due_imports", lambda _executor: 0)
    monkeypatch.setattr(flashscore_worker.research, "_claim_source_point_history_jobs", lambda: [])
    monkeypatch.setattr(flashscore_worker, "_BULK_TURN", 0)
    current, backlog, point, control = flashscore_worker._create_worker_executors(3)
    try:
        asyncio.run(flashscore_worker._dispatch_current_work(current))
        asyncio.run(flashscore_worker._dispatch_bulk_work(backlog, control))
        assert scopes == ["current", "background"]
    finally:
        current.shutdown(wait=True)
        backlog.shutdown(wait=True)
        point.shutdown(wait=True)
        control.shutdown(wait=True)


def test_current_profile_slot_is_used_only_after_match_search_is_idle(monkeypatch):
    from app.flashscore_executor_context import profile_scope
    scopes = []

    async def matches(**_kwargs):
        return {"active_count": 0, "claimed_count": 0}

    async def players(**kwargs):
        scopes.append((profile_scope.get(), kwargs["limit"]))
        return {"active_count": 1, "claimed_count": 1}

    monkeypatch.setattr(flashscore_worker.research, "dispatch_tennis_match_search", matches)
    monkeypatch.setattr(flashscore_worker.research, "dispatch_tennis_player_refresh_jobs", players)
    current, backlog, point, control = flashscore_worker._create_worker_executors(2)
    try:
        player_result, match_result = asyncio.run(
            flashscore_worker._dispatch_current_work(current)
        )
        assert player_result["claimed_count"] == 1
        assert match_result["claimed_count"] == 0
        assert scopes == [("current", 1)]
    finally:
        current.shutdown(wait=True)
        backlog.shutdown(wait=True)
        point.shutdown(wait=True)
        control.shutdown(wait=True)


def test_bulk_categories_rotate_after_each_claim(monkeypatch):
    calls = []
    monkeypatch.setattr(flashscore_worker, "_BULK_TURN", 0)

    async def dispatch(category, _executor, _control):
        calls.append(category)
        return category in {"source_pbp", "historical_profiles"}

    monkeypatch.setattr(flashscore_worker, "_dispatch_bulk_category", dispatch)
    current, backlog, point, control = flashscore_worker._create_worker_executors(3)
    try:
        assert asyncio.run(flashscore_worker._dispatch_bulk_work(backlog, control)) == ("source_pbp", True)
        assert asyncio.run(flashscore_worker._dispatch_bulk_work(backlog, control)) == ("historical_profiles", True)
        assert calls == ["archive", "source_pbp", "historical_profiles"]
    finally:
        current.shutdown(wait=True)
        backlog.shutdown(wait=True)
        point.shutdown(wait=True)
        control.shutdown(wait=True)
