import fcntl
import asyncio
import os
import subprocess
import sys
import time
import types

import pytest

from services.research.app.browser_pool import (
    _BrowserState, ThreadLocalChromium, browser_context_proxy_options,
)


class FakeContext:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.closed = False
        self.contexts = []
        self.context_options = []

    def is_connected(self):
        return not self.closed

    def new_context(self, **kwargs):
        context = FakeContext()
        self.contexts.append(context)
        self.context_options.append(kwargs)
        return context

    def close(self):
        self.closed = True


class FakePlaywright:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def _pool_with_fake_state(max_jobs=50):
    pool = ThreadLocalChromium(max_jobs=max_jobs)
    browser, playwright = FakeBrowser(), FakePlaywright()
    pool._local.state = _BrowserState(playwright, browser, time.monotonic())
    return pool, browser, playwright


def test_reuses_browser_but_isolates_and_closes_each_job_context():
    pool, browser, _playwright = _pool_with_fake_state()

    with pool.context({"headless": True}, locale="en-US") as (yielded_browser, first):
        assert yielded_browser is browser
        assert not first.closed
    with pool.context({"headless": True}, locale="en-US") as (yielded_browser, second):
        assert yielded_browser is browser
        assert second is not first
        assert first.closed

    assert first.closed and second.closed
    assert not browser.closed
    assert pool._local.state.jobs == 2


def test_rotates_browser_at_job_limit():
    pool, browser, playwright = _pool_with_fake_state(max_jobs=2)

    with pool.context({}):
        pass
    with pool.context({}):
        pass

    assert browser.closed
    assert playwright.stopped
    assert not hasattr(pool._local, "state")


def test_failed_job_context_retires_browser():
    pool, browser, playwright = _pool_with_fake_state()

    with pytest.raises(RuntimeError):
        with pool.context({}) as (_browser, context):
            raise RuntimeError("navigation failure")

    assert browser.closed
    assert playwright.stopped
    assert context.closed
    assert not hasattr(pool._local, "state")


def test_handled_navigation_error_can_invalidate_browser():
    pool, browser, playwright = _pool_with_fake_state()

    with pool.context({}):
        pool.invalidate_current()

    assert browser.closed
    assert playwright.stopped
    assert not hasattr(pool._local, "state")


def test_closing_lane_browser_releases_slot_for_the_next_lane(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASHSCORE_BROWSER_PROCESSES", "1")
    monkeypatch.setenv("FLASHSCORE_BROWSER_SLOT_DIR", str(tmp_path))
    monkeypatch.setenv("FLASHSCORE_BROWSER_SLOT_WAIT_SECONDS", "0.2")
    first, browser, playwright = _pool_with_fake_state()
    second = ThreadLocalChromium()
    first._local.state.slot_fd = first._acquire_slot()

    with pytest.raises(TimeoutError, match="FLASHSCORE_BROWSER_SLOT_TIMEOUT"):
        second._acquire_slot()

    first.close_current()
    fd = second._acquire_slot()
    second._release_slot(fd)
    assert browser.closed
    assert playwright.stopped
    assert not hasattr(first._local, "state")


def test_age_limit_restarts_browser_with_a_fresh_driver(monkeypatch):
    pool = ThreadLocalChromium(max_jobs=50, max_age_seconds=1)
    old_browser, old_playwright = FakeBrowser(), FakePlaywright()
    pool._local.state = _BrowserState(old_playwright, old_browser, time.monotonic() - 2)

    new_browser = FakeBrowser()

    class FakeChromium:
        def launch(self, **_kwargs):
            return new_browser

    new_playwright = FakePlaywright()
    new_playwright.chromium = FakeChromium()
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.sync_playwright = lambda: types.SimpleNamespace(start=lambda: new_playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

    with pool.context({"headless": True}) as (browser, context):
        assert browser is new_browser
        assert context is new_browser.contexts[0]

    assert old_browser.closed and old_playwright.stopped
    assert not new_browser.closed


def test_remote_browser_connection_uses_shared_endpoint_and_proxy_context(monkeypatch):
    pool = ThreadLocalChromium()
    browser = FakeBrowser()
    calls = []

    class FakeChromium:
        def connect(self, endpoint, **kwargs):
            calls.append((endpoint, kwargs))
            return browser

        def launch(self, **_kwargs):
            raise AssertionError("remote mode must not launch a local browser")

    playwright = FakePlaywright()
    playwright.chromium = FakeChromium()
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.sync_playwright = lambda: types.SimpleNamespace(start=lambda: playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    monkeypatch.setattr(pool, "_acquire_slot", lambda: 123)
    monkeypatch.setattr(pool, "_release_slot", lambda _fd: None)
    monkeypatch.setenv("PLAYWRIGHT_WS_ENDPOINT", "ws://research-browser:3000/")
    monkeypatch.setenv("RESEARCH_HTTP_PROXY", "http://proxy-user:secret@proxy.example:8080")

    with pool.context({"headless": True}, locale="en-US"):
        pass

    assert calls == [("ws://research-browser:3000/", {"timeout": 30000})]
    assert browser.context_options[0]["proxy"] == {
        "server": "http://proxy.example:8080", "username": "proxy-user", "password": "secret",
    }


def test_browser_context_proxy_options_preserve_uncredentialed_proxy(monkeypatch):
    monkeypatch.setenv("RESEARCH_HTTP_PROXY", "http://127.0.0.1:17890")
    assert browser_context_proxy_options() == {"proxy": {"server": "http://127.0.0.1:17890"}}


def test_browser_slot_lock_is_shared_between_pool_instances(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASHSCORE_BROWSER_PROCESSES", "1")
    monkeypatch.setenv("FLASHSCORE_BROWSER_SLOT_DIR", str(tmp_path))
    monkeypatch.setenv("FLASHSCORE_BROWSER_SLOT_WAIT_SECONDS", "1")
    first_pool = ThreadLocalChromium()
    second_pool = ThreadLocalChromium()
    first_fd = first_pool._acquire_slot()
    try:
        with pytest.raises(TimeoutError, match="FLASHSCORE_BROWSER_SLOT_TIMEOUT"):
            second_pool._acquire_slot()
    finally:
        fcntl.flock(first_fd, fcntl.LOCK_UN)
        os.close(first_fd)

    second_fd = second_pool._acquire_slot()
    fcntl.flock(second_fd, fcntl.LOCK_UN)
    os.close(second_fd)


def test_async_profile_browser_slot_shares_cap_with_sync_pool(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASHSCORE_BROWSER_PROCESSES", "1")
    monkeypatch.setenv("FLASHSCORE_BROWSER_SLOT_DIR", str(tmp_path))
    monkeypatch.setenv("FLASHSCORE_BROWSER_SLOT_WAIT_SECONDS", "0.2")
    pool = ThreadLocalChromium()

    async def run():
        async with pool.browser_slot():
            with pytest.raises(TimeoutError, match="FLASHSCORE_BROWSER_SLOT_TIMEOUT"):
                pool._acquire_slot()

    asyncio.run(run())
    fd = pool._acquire_slot()
    pool._release_slot(fd)


def test_browser_slot_is_released_by_kernel_when_owner_process_is_killed(tmp_path, monkeypatch):
    lock_path = tmp_path / "browser-0.lock"
    child_code = (
        "import fcntl,os,sys,time; "
        "fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600); "
        "fcntl.flock(fd,fcntl.LOCK_EX); print('ready',flush=True); time.sleep(60)"
    )
    child = subprocess.Popen([sys.executable, "-c", child_code, str(lock_path)],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "ready"
        monkeypatch.setenv("FLASHSCORE_BROWSER_PROCESSES", "1")
        monkeypatch.setenv("FLASHSCORE_BROWSER_SLOT_DIR", str(tmp_path))
        monkeypatch.setenv("FLASHSCORE_BROWSER_SLOT_WAIT_SECONDS", "1")
        pool = ThreadLocalChromium()
        with pytest.raises(TimeoutError, match="FLASHSCORE_BROWSER_SLOT_TIMEOUT"):
            pool._acquire_slot()
    finally:
        child.terminate()
        child.wait(timeout=5)

    fd = ThreadLocalChromium()._acquire_slot()
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
