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
HEARTBEAT_STOP = threading.Event()


def _browser_process_limit() -> int:
    """Keep thread-local Playwright instances within the configured browser budget."""
    return max(1, min(4, int(os.getenv("FLASHSCORE_BROWSER_PROCESSES", "2"))))


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


async def _prepare_events() -> int:
    from .main import TennisPreparationRequest
    total = 0
    # Keyset-style bounded batches avoid a 500-event cap while respecting the
    # request contract and keeping each DB/API transaction short.
    for ids in await asyncio.to_thread(_eligible_event_batches):
        if STOP:
            break
        await research.prepare_tennis_event_players(TennisPreparationRequest(event_ids=ids))
        total += len(ids)
    return total


def _schedule_due_imports() -> int:
    from db.connection import SessionLocal
    from db.models import FlashscoreImportJob

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        ids = [row[0] for row in (session.query(FlashscoreImportJob.id)
            .filter(FlashscoreImportJob.next_run_at <= now)
            .filter((FlashscoreImportJob.status == "queued") |
                    ((FlashscoreImportJob.status == "running") & (FlashscoreImportJob.lease_until <= now)))
            .order_by(FlashscoreImportJob.next_run_at, FlashscoreImportJob.created_at)
            .limit(4).all())]
    scheduled = 0
    for job_id in ids:
        key = str(job_id)
        if key not in IMPORT_TASKS and len(IMPORT_TASKS) < 2:
            IMPORT_TASKS.add(key)
            task = asyncio.create_task(research._run_flashscore_import(job_id))
            task.add_done_callback(lambda _task, job_key=key: IMPORT_TASKS.discard(job_key))
            scheduled += 1
    return scheduled


async def main() -> None:
    global STOP
    logger.info("Dedicated Flashscore executor started")
    browser_limit = _browser_process_limit()
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(
        max_workers=browser_limit,
        thread_name_prefix="flashscore-browser",
    ))
    logger.info("Flashscore browser thread/process budget: %d", browser_limit)
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, name="flashscore-executor-heartbeat", daemon=True
    )
    heartbeat_thread.start()
    discovery_interval = max(5, int(os.getenv("FLASHSCORE_DISCOVERY_SECONDS", "15")))
    last_discovery = 0.0
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
                reconciled = await asyncio.to_thread(research.auto_resolve_exact_match_confirmations)
                if reconciled.get("resolved"):
                    logger.info("Auto-resolved exact confirmed match candidates: %s", reconciled)

            if now_mono - last_discovery >= discovery_interval:
                last_discovery = now_mono
                count = await _prepare_events()
                logger.info("Prepared eligible tennis events: %d", count)
                # Live PBP enrollment stays on the fast discovery loop. The
                # historical scan walks the whole completed archive, so run it
                # on its own slower cadence instead of repeating it every 15s.
                pbp = await research.dispatch_live_point_history(limit=30)
                logger.info("Point history dispatch: %s", pbp)

            if now_mono - last_archive_discovery >= archive_discovery_interval:
                last_archive_discovery = now_mono
                archive = await research.dispatch_tennis_archive_backfill(limit=4)
                logger.info("Historical PBP backfill: %s", archive)

            if now_mono - last_import_enqueued >= import_interval and research.config.flashscore_tennis_profile_urls:
                last_import_enqueued = now_mono
                await research.import_flashscore_tennis_history()

            players = await research.dispatch_tennis_player_refresh_jobs(limit=4)
            match_search = await research.dispatch_tennis_match_search(limit=4)
            imports = _schedule_due_imports()
            if players.get("claimed_count") or match_search.get("claimed_count") or imports:
                logger.info("Flashscore jobs dispatched players=%s match_search=%s imports=%s",
                    players.get("claimed_count"), match_search.get("claimed_count"), imports)
        except Exception:
            logger.exception("Flashscore executor iteration failed; durable jobs remain queued")
        await asyncio.sleep(2)
    if IMPORT_TASKS:
        await asyncio.gather(*[task for task in asyncio.all_tasks() if task is not asyncio.current_task()], return_exceptions=True)
    HEARTBEAT_STOP.set()
    heartbeat_thread.join(timeout=2)
    logger.info("Dedicated Flashscore executor stopped")


if __name__ == "__main__":
    asyncio.run(main())
