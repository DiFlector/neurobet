"""Dedicated durable executor for tennis/Flashscore jobs.

The HTTP service keeps the queue and handlers; this process owns dispatch so
browser work cannot delay API requests or the general Redis worker.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import signal
import threading
from datetime import datetime, timedelta, timezone

from . import main as research

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s [%(levelname)s] [flashscore-worker] %(message)s")
logger = logging.getLogger("flashscore-worker")
STOP = False
IMPORT_TASKS: set[str] = set()
SOURCE_TASKS: set[str] = set()
_BULK_TURN = 0
HEARTBEAT_STOP = threading.Event()


def _browser_process_limit() -> int:
    """Keep thread-local Playwright instances within the configured browser budget."""
    return max(1, min(4, int(os.getenv("FLASHSCORE_BROWSER_PROCESSES", "2"))))


def _point_worker_limit(browser_limit: int) -> int:
    """Keep one browser worker available for profiles/archive work."""
    return max(1, min(
        max(1, int(browser_limit) - 1),
        int(os.getenv("FLASHSCORE_TENNIS_POINT_CONCURRENCY", "1")),
    ))


def _create_worker_executors(browser_limit: int):
    """Reserve live, current-match, and backlog lanes within the browser budget."""
    point_workers = _point_worker_limit(browser_limit)
    background_workers = max(1, int(browser_limit) - point_workers)
    current = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flashscore-current-browser")
    backlog = (ThreadPoolExecutor(max_workers=1, thread_name_prefix="flashscore-backlog-browser")
               if background_workers >= 2 else current)
    point = ThreadPoolExecutor(max_workers=point_workers, thread_name_prefix="flashscore-live-point")
    control = ThreadPoolExecutor(max_workers=2, thread_name_prefix="flashscore-control")
    return current, backlog, point, control


def _write_executor_heartbeat() -> None:
    from sqlalchemy import text
    from db.connection import SessionLocal
    with SessionLocal() as session:
        session.execute(text("UPDATE flashscore_source_control SET executor_heartbeat_at=now(), updated_at=now() WHERE id=1"))
        session.commit()


def _write_health_file() -> None:
    with open("/tmp/healthy", "w") as health:
        health.write(datetime.now(timezone.utc).isoformat())


def _heartbeat_loop() -> None:
    """Keep executor liveness independent from long async/browser dispatch work.

    The main loop can spend minutes waiting for a database lock or coordinating
    Chromium tasks. A heartbeat tied to that loop makes the queue report a
    healthy, working executor as dead. This small thread owns only the
    liveness write and never touches job leases or results.
    """
    while not HEARTBEAT_STOP.is_set():
        try:
            _write_executor_heartbeat()
            _write_health_file()
        except Exception:
            logger.exception("Flashscore executor heartbeat failed")
        HEARTBEAT_STOP.wait(10)


def _stop(*_args):
    global STOP
    STOP = True


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


def _eligible_event_batches() -> list[list[str]]:
    """Select live events with fresh Fonbet observations and prematch +6h."""
    from db.connection import SessionLocal
    from db.models import Event
    from sqlalchemy import or_

    now = datetime.now(timezone.utc)
    live_fresh_after = now - timedelta(seconds=90)
    with SessionLocal() as session:
        rows = (session.query(Event.id)
            .filter(Event.sport_code == "tennis", Event.finished_at.is_(None))
            .filter(~Event.participant_a_name.contains("/"), ~Event.participant_b_name.contains("/"))
            .filter(or_(
                (Event.is_live.is_(True) & (Event.last_seen_at >= live_fresh_after)),
                (Event.is_live.is_(False) & (Event.scheduled_start_at >= now) & (Event.scheduled_start_at <= now + timedelta(hours=6))),
            ))
            .order_by(Event.id).all())
    ids = [str(row[0]) for row in rows]
    return [ids[i:i + 1000] for i in range(0, len(ids), 1000)]


async def _prepare_events(control_executor=None) -> int:
    from .main import TennisPreparationRequest
    total = 0
    # Keyset-style bounded batches avoid a 500-event cap while respecting the
    # request contract and keeping each DB/API transaction short.
    loop = asyncio.get_running_loop()
    for ids in await loop.run_in_executor(control_executor, _eligible_event_batches):
        if STOP:
            break
        await research.prepare_tennis_event_players(TennisPreparationRequest(event_ids=ids))
        total += len(ids)
    return total


def _schedule_due_imports(executor=None) -> int:
    from db.connection import SessionLocal
    from db.models import FlashscoreImportJob

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        ids = [row[0] for row in (session.query(FlashscoreImportJob.id)
            .filter(FlashscoreImportJob.next_run_at <= now)
            .filter((FlashscoreImportJob.status == "queued") |
                    ((FlashscoreImportJob.status == "running") & (FlashscoreImportJob.lease_until <= now)))
            .order_by(FlashscoreImportJob.next_run_at, FlashscoreImportJob.created_at)
            .limit(1).all())]
    scheduled = 0
    for job_id in ids:
        key = str(job_id)
        if key not in IMPORT_TASKS and len(IMPORT_TASKS) < 1:
            IMPORT_TASKS.add(key)
            task = asyncio.create_task(research._run_flashscore_import(job_id, executor=executor))
            task.add_done_callback(lambda _task, job_key=key: IMPORT_TASKS.discard(job_key))
            scheduled += 1
    return scheduled


async def _dispatch_current_work(executor) -> tuple[dict, dict]:
    """Run ready match lookups on the current lane; profiles stay background work."""
    from .flashscore_executor_context import current_executor, executor_busy
    if executor_busy(executor):
        return {}, {}
    executor_token = current_executor.set(executor)
    try:
        match_search = await research.dispatch_tennis_match_search(limit=1)
        return {}, match_search
    finally:
        current_executor.reset(executor_token)


async def _dispatch_bulk_category(category: str, executor, control_executor) -> bool:
    from .flashscore_executor_context import current_executor, executor_busy, profile_scope, submit_blocking
    if executor_busy(executor):
        return False
    if category == "archive":
        return bool(_schedule_due_imports(executor))
    if category == "source_pbp":
        loop = asyncio.get_running_loop()
        source_jobs = await loop.run_in_executor(
            control_executor, research._claim_source_point_history_jobs
        )
        if not source_jobs:
            return False
        source_match_id, source_url, token = source_jobs[0]
        SOURCE_TASKS.add(source_match_id)
        executor_token = current_executor.set(executor)
        try:
            future = submit_blocking(research._run_source_point_refresh,
                                     source_match_id, source_url, token)
        finally:
            current_executor.reset(executor_token)
        future.add_done_callback(lambda _done, key=source_match_id: SOURCE_TASKS.discard(key))
        return True
    if category == "historical_profiles":
        executor_token = current_executor.set(executor)
        # Current and background participant refreshes are both lower priority
        # than match search, live PBP, source PBP, and archive page fetches.
        scope_token = profile_scope.set("any")
        try:
            result = await research.dispatch_tennis_player_refresh_jobs(limit=1)
        finally:
            profile_scope.reset(scope_token)
            current_executor.reset(executor_token)
        return bool(result.get("claimed_count", 0))
    return False


async def _dispatch_bulk_work(executor, control_executor) -> tuple[str | None, bool]:
    """Round-robin archives, source PBP, and non-current profile refreshes."""
    global _BULK_TURN
    categories = ("archive", "source_pbp", "historical_profiles")
    for offset in range(len(categories)):
        index = (_BULK_TURN + offset) % len(categories)
        category = categories[index]
        if await _dispatch_bulk_category(category, executor, control_executor):
            _BULK_TURN = (index + 1) % len(categories)
            return category, True
    return None, False


async def main() -> None:
    global STOP
    logger.info("Dedicated Flashscore executor started")
    browser_limit = _browser_process_limit()
    loop = asyncio.get_running_loop()
    # Keep one of the configured Chromium slots available for live PBP. Profile
    # refresh and archive import share the remaining browser lane; their long
    # fetches must not occupy the thread that dispatches current match history.
    current_executor, backlog_executor, point_executor, control_executor = _create_worker_executors(browser_limit)
    loop.set_default_executor(current_executor)
    separate_backlog_lane = backlog_executor is not current_executor
    logger.info(
        "Flashscore browser budget: %d total (%d live PBP, %d current, %d backlog)",
        browser_limit, _point_worker_limit(browser_limit),
        1, int(separate_backlog_lane),
    )
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, name="flashscore-executor-heartbeat", daemon=True
    )
    heartbeat_thread.start()
    discovery_interval = max(5, int(os.getenv("FLASHSCORE_DISCOVERY_SECONDS", "15")))
    last_discovery = 0.0
    preparation_interval = max(30, int(os.getenv("FLASHSCORE_PREPARATION_SECONDS", "60")))
    last_preparation = 0.0
    archive_discovery_interval = max(60, int(os.getenv("FLASHSCORE_ARCHIVE_DISCOVERY_SECONDS", "900")))
    last_archive_discovery = 0.0
    import_interval = max(3600, int(os.getenv("FLASHSCORE_TENNIS_IMPORT_INTERVAL_SECONDS", "86400")))
    last_import_enqueued = 0.0
    last_confirmation_reconcile = 0.0
    while not STOP:
        now_mono = asyncio.get_running_loop().time()
        try:
            if now_mono - last_confirmation_reconcile >= 60:
                last_confirmation_reconcile = now_mono
                reconciled = await loop.run_in_executor(
                    control_executor, research.auto_resolve_exact_match_confirmations
                )
                if reconciled.get("resolved"):
                    logger.info("Auto-resolved exact confirmed match candidates: %s", reconciled)

            if now_mono - last_discovery >= discovery_interval:
                last_discovery = now_mono
                # Live PBP enrollment stays on the fast discovery loop. The
                # current-state dispatcher and its browser lane run before
                # player preparation can wait on background browser work.
                # The historical scan walks the whole completed archive, so
                # it runs on a separate slower cadence below.
                pbp = await research._dispatch_live_point_history(
                    limit=1, executor=point_executor
                )
                logger.info("Point history dispatch: %s", pbp)
                if now_mono - last_preparation >= preparation_interval:
                    last_preparation = now_mono
                    count = await _prepare_events(control_executor)
                    logger.info("Prepared eligible tennis events: %d", count)

            if now_mono - last_archive_discovery >= archive_discovery_interval:
                last_archive_discovery = now_mono
                archive = await research.dispatch_tennis_archive_backfill(limit=4)
                logger.info("Historical PBP backfill: %s", archive)

            if (not SOURCE_TASKS and now_mono - last_import_enqueued >= import_interval
                    and research.config.flashscore_tennis_profile_urls):
                last_import_enqueued = now_mono
                await research.import_flashscore_tennis_history()

            players, match_search = await _dispatch_current_work(current_executor)
            bulk_category, bulk_claimed = (None, False)
            if separate_backlog_lane:
                bulk_category, bulk_claimed = await _dispatch_bulk_work(
                    backlog_executor, control_executor
                )
            elif not (players.get("active_count") or match_search.get("active_count")):
                bulk_category, bulk_claimed = await _dispatch_bulk_work(
                    current_executor, control_executor
                )
            if players.get("claimed_count") or match_search.get("claimed_count") or bulk_claimed:
                logger.info("Flashscore jobs dispatched current_players=%s match_search=%s bulk=%s category=%s",
                    players.get("claimed_count"), match_search.get("claimed_count"),
                    int(bulk_claimed), bulk_category)
        except Exception:
            logger.exception("Flashscore executor iteration failed; durable jobs remain queued")
        await asyncio.sleep(2)
    if IMPORT_TASKS:
        await asyncio.gather(*[task for task in asyncio.all_tasks() if task is not asyncio.current_task()], return_exceptions=True)
    HEARTBEAT_STOP.set()
    heartbeat_thread.join(timeout=2)
    if separate_backlog_lane:
        backlog_executor.shutdown(wait=False, cancel_futures=False)
    current_executor.shutdown(wait=False, cancel_futures=False)
    point_executor.shutdown(wait=False, cancel_futures=False)
    control_executor.shutdown(wait=False, cancel_futures=False)
    logger.info("Dedicated Flashscore executor stopped")


if __name__ == "__main__":
    asyncio.run(main())
