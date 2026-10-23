from __future__ import annotations

import asyncio
import inspect
import threading

from app import flashscore_worker


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
    assert current is backlog
    assert current._max_workers == point._max_workers == 1
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


def test_profiles_are_dispatched_only_in_low_priority_background_scope(monkeypatch):
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
        assert scopes == ["any"]
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
