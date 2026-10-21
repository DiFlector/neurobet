from __future__ import annotations

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
