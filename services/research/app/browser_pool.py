"""Thread-local reusable Playwright browsers with isolated per-job contexts.

The Flashscore executor runs synchronous browser work via ``asyncio.to_thread``.
Each executor thread owns its Playwright driver and Chromium process, while each
job receives a fresh browser context so cookies, storage, and page state never
leak between matches. Browsers rotate periodically to bound stale state.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import os
import fcntl
import asyncio
import threading
import time
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlsplit, urlunsplit

logger = logging.getLogger("research.browser_pool")


def playwright_browser_endpoint() -> str | None:
    return os.getenv("PLAYWRIGHT_WS_ENDPOINT") or None


def browser_context_proxy_options() -> dict[str, Any]:
    """Build Playwright context proxy settings from the shared source proxy URL."""
    proxy_url = os.getenv("RESEARCH_HTTP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    if not proxy_url:
        return {}
    parsed = urlsplit(proxy_url)
    if not parsed.scheme or not parsed.hostname:
        return {"proxy": {"server": proxy_url}}
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    server = urlunsplit((parsed.scheme, f"{host}:{parsed.port}" if parsed.port else host,
                         "", "", ""))
    proxy: dict[str, str] = {"server": server}
    if parsed.username is not None:
        proxy["username"] = unquote(parsed.username)
    if parsed.password is not None:
        proxy["password"] = unquote(parsed.password)
    return {"proxy": proxy}


@dataclass
class _BrowserState:
    playwright: Any
    browser: Any
    started_at: float
    slot_fd: int | None = None
    jobs: int = 0


class ThreadLocalChromium:
    """Reuse one Chromium process per calling thread; contexts remain per job."""

    def __init__(self, *, max_jobs: int = 50, max_age_seconds: float = 900.0):
        self.max_jobs = max(1, int(max_jobs))
        self.max_age_seconds = max(1.0, float(max_age_seconds))
        self.max_processes = max(1, min(4, int(os.getenv("FLASHSCORE_BROWSER_PROCESSES", "2"))))
        self.slot_dir = Path(os.getenv("FLASHSCORE_BROWSER_SLOT_DIR", "/run/flashscore-browser-slots"))
        self.slot_wait_seconds = max(1.0, float(os.getenv("FLASHSCORE_BROWSER_SLOT_WAIT_SECONDS", "300")))
        self._local = threading.local()

    def _acquire_slot(self) -> int:
        """Acquire one cross-process browser lease using kernel-released file locks."""
        self.slot_dir.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.slot_wait_seconds
        while time.monotonic() < deadline:
            fd = self._try_acquire_slot()
            if fd is not None:
                return fd
            time.sleep(0.1)
        raise TimeoutError("FLASHSCORE_BROWSER_SLOT_TIMEOUT")

    def _try_acquire_slot(self) -> int | None:
        self.slot_dir.mkdir(parents=True, exist_ok=True)
        for index in range(self.max_processes):
            fd = os.open(self.slot_dir / f"browser-{index}.lock", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                os.close(fd)
        return None

    @asynccontextmanager
    async def browser_slot(self) -> Iterator[None]:
        """Reserve a global browser slot for async Playwright launchers too."""
        deadline = time.monotonic() + self.slot_wait_seconds
        fd = None
        while time.monotonic() < deadline:
            fd = self._try_acquire_slot()
            if fd is not None:
                break
            await asyncio.sleep(0.1)
        if fd is None:
            raise TimeoutError("FLASHSCORE_BROWSER_SLOT_TIMEOUT")
        try:
            yield
        finally:
            self._release_slot(fd)

    @staticmethod
    def _release_slot(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _close_state(self, state: _BrowserState | None) -> None:
        if state is None:
            return
        try:
            if state.browser.is_connected():
                state.browser.close()
        except Exception:
            logger.debug("Could not close retired Chromium", exc_info=True)
        try:
            state.playwright.stop()
        except Exception:
            logger.debug("Could not stop retired Playwright driver", exc_info=True)
        if state.slot_fd is not None:
            try:
                self._release_slot(state.slot_fd)
            except OSError:
                logger.debug("Could not release retired Chromium slot", exc_info=True)

    def _state(self, launch_options: dict[str, Any]) -> _BrowserState:
        state = getattr(self._local, "state", None)
        now = time.monotonic()
        expired = state is not None and (
            state.jobs >= self.max_jobs or now - state.started_at >= self.max_age_seconds
        )
        disconnected = state is not None and not state.browser.is_connected()
        if expired or disconnected:
            self._close_state(state)
            state = None
        if state is None:
            from playwright.sync_api import sync_playwright

            slot_fd = self._acquire_slot()
            playwright = None
            try:
                playwright = sync_playwright().start()
                endpoint = playwright_browser_endpoint()
                browser = (playwright.chromium.connect(endpoint, timeout=30000)
                           if endpoint else playwright.chromium.launch(**launch_options))
            except Exception:
                try:
                    if playwright is not None:
                        playwright.stop()
                except Exception:
                    logger.debug("Could not stop failed Playwright startup", exc_info=True)
                finally:
                    self._release_slot(slot_fd)
                raise
            state = _BrowserState(playwright, browser, now, slot_fd)
            self._local.state = state
            logger.info("Started reusable Flashscore Chromium for worker thread")
        return state

    def invalidate_current(self) -> None:
        """Retire this thread's browser after a handled navigation/renderer error."""
        self._local.retire_current = True

    def close_current(self) -> None:
        """Close this thread's idle browser and release its shared slot lock."""
        state = getattr(self._local, "state", None)
        if state is None:
            return
        self._close_state(state)
        if getattr(self._local, "state", None) is state:
            del self._local.state
        self._local.retire_current = False

    @contextmanager
    def context(self, launch_options: dict[str, Any], **context_options: Any) -> Iterator[tuple[Any, Any]]:
        """Yield ``(browser, context)`` and always discard the job context."""
        state = self._state(launch_options)
        if playwright_browser_endpoint():
            context_options.setdefault("proxy", browser_context_proxy_options().get("proxy"))
        try:
            context = state.browser.new_context(**context_options)
        except Exception:
            self._close_state(state)
            if getattr(self._local, "state", None) is state:
                del self._local.state
            raise
        failed = False
        self._local.retire_current = False
        try:
            yield state.browser, context
        except Exception:
            # Navigation/renderer failures can leave a browser process unhealthy.
            # Retire it after closing this context; the next job starts clean.
            failed = True
            raise
        finally:
            try:
                context.close()
            except Exception:
                logger.debug("Could not close Flashscore job context", exc_info=True)
            state.jobs += 1
            retire = failed or bool(getattr(self._local, "retire_current", False))
            if (retire or state.jobs >= self.max_jobs
                    or time.monotonic() - state.started_at >= self.max_age_seconds):
                self._close_state(state)
                if getattr(self._local, "state", None) is state:
                    del self._local.state
            self._local.retire_current = False


flashscore_chromium = ThreadLocalChromium(
    max_jobs=int(os.getenv("FLASHSCORE_BROWSER_MAX_JOBS", "50")),
    max_age_seconds=float(os.getenv("FLASHSCORE_BROWSER_MAX_AGE_SECONDS", "900")),
)
