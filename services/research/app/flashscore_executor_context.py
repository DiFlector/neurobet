"""Private executor routing for the dedicated Flashscore worker.

HTTP dispatch routes keep their existing signatures. The worker sets a context
for the lane that owns the lease; API callers without a lane keep using the
service's normal asyncio thread pool.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Executor, Future
from contextvars import ContextVar
import threading
from typing import Any, Callable


current_executor: ContextVar[Executor | None] = ContextVar("flashscore_current_executor", default=None)
profile_scope: ContextVar[str] = ContextVar("flashscore_profile_scope", default="any")
_futures: dict[int, set[Future[Any]]] = {}
_lock = threading.Lock()


def submit_blocking(function: Callable[..., Any], *args: Any) -> asyncio.Future[Any] | asyncio.Task[Any]:
    """Run a leased job on its assigned worker lane and retain its future."""
    executor = current_executor.get()
    if executor is None:
        return asyncio.create_task(asyncio.to_thread(function, *args))
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(executor, function, *args)
    key = id(executor)
    with _lock:
        _futures.setdefault(key, set()).add(future)

    def discard(done: Future[Any]) -> None:
        with _lock:
            active = _futures.get(key)
            if active is not None:
                active.discard(done)
                if not active:
                    _futures.pop(key, None)
        try:
            done.result()
        except Exception:
            import logging
            logging.getLogger("research.flashscore_executor").exception("Flashscore lane job failed")

    future.add_done_callback(discard)
    return future


def executor_busy(executor: Executor) -> bool:
    with _lock:
        return bool(_futures.get(id(executor)))
