"""FastAPI HTTP service for Neurobet Web Research."""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import multiprocessing
import os
import threading
import time
from functools import partial
from typing import Any, Dict, List
import uuid
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from contracts import ResearchPacket, ResearchRequest
from .config import config
from .service import WebResearchService
from .tennis_flashscore import FlashscoreTennisImporter, PARSER_VERSION, is_doubles_player_display

# Application timestamps, DB availability times, and structured logs all use
# UTC even when the container's display timezone is configured for an operator.
logging.Formatter.converter = time.gmtime
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s UTC [%(levelname)s] [research] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("research")

app = FastAPI(
    title="Neurobet Web Research Service",
    description="Contextual web research and extraction pipeline",
    version="1.0.0",
)

try:
    from observability import setup_observability
    setup_observability(app, service_name="research")
except ImportError:
    pass

research_service = WebResearchService()
flashscore_import_tasks: set[str] = set()
_flashscore_lock_engine = None


def _record_flashscore_throttle() -> None:
    """Apply a conservative global cooldown after a source-side 403/429 signal."""
    from sqlalchemy import text
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from db.connection import get_database_url

    global _flashscore_lock_engine
    if _flashscore_lock_engine is None:
        _flashscore_lock_engine = create_engine(get_database_url(), poolclass=NullPool, pool_pre_ping=True)
    with _flashscore_lock_engine.begin() as connection:
        connection.execute(text("""
            UPDATE flashscore_source_control
            SET cooldown_until=greatest(coalesce(cooldown_until, now()), now()+interval '15 minutes'),
                last_throttle_at=now(), success_count=0, slot_limit=1, updated_at=now()
            WHERE id=1
        """))


@contextmanager
def _flashscore_source_access(priority: str = "participant"):
    """Claim one of the globally rate-limited durable Flashscore network slots."""
    global _flashscore_lock_engine
    import uuid as uuid_module
    from sqlalchemy import text
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from db.connection import get_database_url
    if _flashscore_lock_engine is None:
        _flashscore_lock_engine = create_engine(get_database_url(), poolclass=NullPool, pool_pre_ping=True)
    request_id = uuid_module.uuid4()
    priorities = {"live": 0, "participant": 10, "archive": 20}
    if priority not in priorities:
        raise ValueError(f"Unknown Flashscore request priority: {priority}")
    priority_value = priorities[priority]
    with _flashscore_lock_engine.begin() as connection:
        connection.execute(text("DELETE FROM flashscore_source_requests WHERE lease_until <= now() OR heartbeat_at < now() - interval '15 seconds'"))
        connection.execute(text("INSERT INTO flashscore_source_requests (id, priority, enqueued_at, lease_until, heartbeat_at) VALUES (:id, :priority, now(), now() + interval '6 minutes', now())"), {"id": request_id, "priority": priority_value})

    heartbeat_stop = threading.Event()

    def heartbeat():
        while not heartbeat_stop.wait(5):
            try:
                with _flashscore_lock_engine.begin() as connection:
                    connection.execute(text("UPDATE flashscore_source_requests SET lease_until = now() + interval '6 minutes', heartbeat_at = now() WHERE id = :id AND lease_until > now()"), {"id": request_id})
            except Exception:
                logger.exception("Flashscore source queue heartbeat failed request=%s", request_id)
                return

    heartbeat_thread = threading.Thread(target=heartbeat, name="flashscore-source-heartbeat", daemon=True)
    heartbeat_thread.start()
    deadline = time.monotonic() + max(1.0, float(os.getenv("FLASHSCORE_SOURCE_LOCK_WAIT_SECONDS", "300")))
    acquired = False
    try:
        while time.monotonic() < deadline:
            with _flashscore_lock_engine.begin() as connection:
                connection.execute(text("DELETE FROM flashscore_source_requests WHERE lease_until <= now() OR heartbeat_at < now() - interval '15 seconds'"))
                row = connection.execute(text("SELECT slot_limit, cooldown_until, next_start_at FROM flashscore_source_control WHERE id = 1 FOR UPDATE")).mappings().one()
                has_priority = connection.execute(text("""
                    SELECT NOT EXISTS (
                      SELECT 1 FROM flashscore_source_requests r
                      JOIN flashscore_source_requests me ON me.id = :id
                      WHERE r.id <> me.id AND r.acquired_at IS NULL AND r.lease_until > now()
                        AND r.heartbeat_at >= now() - interval '15 seconds'
                        AND (CASE WHEN r.priority=20 AND r.enqueued_at <= now()-make_interval(secs => :archive_wait) THEN 5 ELSE r.priority END,
                             r.enqueued_at, r.id) <
                            (CASE WHEN me.priority=20 AND me.enqueued_at <= now()-make_interval(secs => :archive_wait) THEN 5 ELSE me.priority END,
                             me.enqueued_at, me.id)
                    )
                """), {"id": request_id, "archive_wait": max(1.0, float(os.getenv("FLASHSCORE_ARCHIVE_MAX_WAIT_SECONDS", "10")))}).scalar()
                active = int(connection.execute(text("SELECT count(*) FROM flashscore_source_requests WHERE acquired_at IS NOT NULL AND lease_until > now() AND heartbeat_at >= now()-interval '15 seconds'")).scalar() or 0)
                allowed = (not row["cooldown_until"] or row["cooldown_until"] <= datetime.now(timezone.utc))
                due_start = row["next_start_at"] <= datetime.now(timezone.utc)
                if has_priority and allowed and due_start and active < int(row["slot_limit"]):
                    slot_number = active + 1
                    connection.execute(text("UPDATE flashscore_source_requests SET acquired_at=now(), slot_number=:slot WHERE id=:id AND acquired_at IS NULL"), {"slot": slot_number, "id": request_id})
                    connection.execute(text("UPDATE flashscore_source_control SET next_start_at=now()+interval '2 seconds', updated_at=now() WHERE id=1"))
                    acquired = True
            if acquired:
                break
            time.sleep(0.2)
        if not acquired:
            raise TimeoutError("FLASHSCORE_SOURCE_LOCK_TIMEOUT")
        # No database connection or transaction is held during external I/O.
        try:
            yield
        except BaseException as exc:
            detail = str(exc).upper()
            if "429" in detail or "403" in detail or "BLOCKED" in detail or "RATE_LIMIT" in detail:
                _record_flashscore_throttle()
            raise
        else:
            with _flashscore_lock_engine.begin() as connection:
                connection.execute(text("""
                    UPDATE flashscore_source_control SET
                      success_count = CASE WHEN last_throttle_at IS NULL OR last_throttle_at <= now()-interval '15 minutes' THEN success_count+1 ELSE 0 END,
                      slot_limit = CASE WHEN success_count+1 >= 20 AND (last_throttle_at IS NULL OR last_throttle_at <= now()-interval '15 minutes') THEN 2 ELSE slot_limit END,
                      cooldown_until = CASE WHEN cooldown_until <= now() THEN NULL ELSE cooldown_until END,
                      updated_at=now() WHERE id=1
                """))
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=2)
        try:
            with _flashscore_lock_engine.begin() as connection:
                connection.execute(text("DELETE FROM flashscore_source_requests WHERE id=:id"), {"id": request_id})
        except Exception:
            logger.exception("Could not release Flashscore network slot request=%s", request_id)


def _flashscore_profile_scrape_process(job_id, attempt_token: str, profile_index: int,
                                       profile_url: str, resume_pages: list[dict[str, Any]], send_conn) -> None:
    """Scrape one profile in a killable process; persist only page checkpoints here."""
    from db.connection import SessionLocal
    from db.models import FlashscoreImportJob

    def page_checkpoint(page_info):
        with SessionLocal() as session:
            owner = session.query(FlashscoreImportJob).filter_by(
                id=job_id, attempt_token=attempt_token, status="running"
            ).filter(FlashscoreImportJob.lease_until > datetime.now(timezone.utc)).with_for_update().first()
            if owner is None:
                raise RuntimeError("FLASHSCORE_IMPORT_LEASE_LOST")
            progress = dict(owner.progress_json or {})
            checkpoints = dict(progress.get("page_checkpoints") or {})
            prior = checkpoints.get(str(profile_index)) or {}
            pages = list(prior.get("pages") or []) if prior.get("parser_version") == PARSER_VERSION else []
            if page_info.get("reset_checkpoint"):
                pages = []
            if page_info.get("archived_path") and page_info.get("response_status") == 200:
                pages = [row for row in pages if row.get("page_index") != page_info.get("page_index")]
                pages.append(page_info)
                pages.sort(key=lambda row: row.get("page_index", 0))
            checkpoints[str(profile_index)] = {"parser_version": PARSER_VERSION, "pages": pages}
            progress.update({
                "current_profile_index": profile_index,
                "current_profile_url": profile_url,
                "last_archived_page": page_info,
                "page_checkpoints": checkpoints,
            })
            owner.progress_json = progress
            owner.lease_until = datetime.now(timezone.utc) + timedelta(minutes=5)
            owner.updated_at = datetime.now(timezone.utc)
            session.commit()

    try:
        importer = FlashscoreTennisImporter()
        result = importer.scrape_profile(
            profile_url,
            page_checkpoint=page_checkpoint,
            source_access=partial(_flashscore_source_access, "archive"),
            resume_pages=resume_pages,
        )
        send_conn.send(("result", result))
    except BaseException as exc:
        try:
            send_conn.send(("error", f"{type(exc).__name__}:{str(exc)[:240]}"))
        except Exception:
            pass
    finally:
        send_conn.close()


def _scrape_flashscore_profile_isolated(job_id, attempt_token: str, profile_index: int,
                                        profile_url: str, resume_pages: list[dict[str, Any]]):
    """Run Chromium in a process that is forcibly stopped at the profile deadline."""
    from .tennis_flashscore import ScrapeResult
    timeout_seconds = max(30, int(os.getenv("FLASHSCORE_TENNIS_PROFILE_TIMEOUT_SECONDS", "300")))
    context = multiprocessing.get_context("spawn")
    recv_conn, send_conn = context.Pipe(duplex=False)
    process = context.Process(
        target=_flashscore_profile_scrape_process,
        args=(job_id, attempt_token, profile_index, profile_url, resume_pages, send_conn),
        name=f"flashscore-profile-{profile_index}",
    )
    process.start()
    send_conn.close()
    deadline = time.monotonic() + timeout_seconds
    message = None
    while process.is_alive() and message is None:
        if recv_conn.poll(0.2):
            try:
                message = recv_conn.recv()
            except EOFError:
                break
        if time.monotonic() >= deadline:
            break
    if process.is_alive() and message is None:
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
        recv_conn.close()
        return ScrapeResult(
            status="unavailable", url=profile_url, retrieved_at=datetime.now(timezone.utc),
            content_hash=None, reason="PROFILE_TIMEOUT_PROCESS_TERMINATED",
        )
    if process.is_alive():
        # A result may arrive just before the spawned process finishes its
        # final cleanup. Wait for exit, then force-stop a wedged child.
        process.join(5)
    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
    if not process.is_alive():
        process.join()
    try:
        if message is None and recv_conn.poll(1):
            try:
                message = recv_conn.recv()
            except EOFError:
                message = None
        if message is not None:
            kind, payload = message
            if kind == "result":
                return payload
            return ScrapeResult(
                status="unavailable", url=profile_url, retrieved_at=datetime.now(timezone.utc),
                content_hash=None, reason=str(payload),
            )
    finally:
        recv_conn.close()
    return ScrapeResult(
        status="unavailable", url=profile_url, retrieved_at=datetime.now(timezone.utc),
        content_hash=None, reason=f"PROFILE_WORKER_EXITED_{process.exitcode}",
    )


def _player_refresh_scrape_process(participant_id, attempt_token: str, profile_url: str,
                                   resume_pages: list[dict[str, Any]], stop_before_date, send_conn) -> None:
    """Fetch a player profile in a killable child and checkpoint under its lease."""
    from db.connection import SessionLocal
    from db.models import TennisPlayerRefreshJob

    def page_checkpoint(page_info):
        with SessionLocal() as session:
            now = datetime.now(timezone.utc)
            owner = session.query(TennisPlayerRefreshJob).filter_by(
                participant_id=participant_id, attempt_token=attempt_token, status="running"
            ).filter(TennisPlayerRefreshJob.lease_until > now).with_for_update().first()
            if owner is None:
                raise RuntimeError("PLAYER_REFRESH_LEASE_LOST")
            progress = dict(owner.progress_json or {})
            checkpoint = progress.get("page_checkpoint") or {}
            pages = list(checkpoint.get("pages") or []) if checkpoint.get("parser_version") == PARSER_VERSION else []
            if page_info.get("reset_checkpoint"):
                pages = []
            if page_info.get("archived_path") and page_info.get("response_status") == 200:
                pages = [row for row in pages if row.get("page_index") != page_info.get("page_index")]
                pages.append(page_info)
                pages.sort(key=lambda row: row.get("page_index", 0))
            progress["page_checkpoint"] = {"parser_version": PARSER_VERSION, "pages": pages}
            progress["last_archived_page"] = page_info
            owner.progress_json = progress
            owner.lease_until = now + timedelta(minutes=5)
            owner.updated_at = now
            session.commit()

    try:
        importer = FlashscoreTennisImporter()
        result = importer.scrape_profile(
            profile_url,
            stop_before_date=stop_before_date,
            page_checkpoint=page_checkpoint,
            resume_pages=resume_pages,
            source_access=partial(_flashscore_source_access, "participant"),
        )
        send_conn.send(("result", result))
    except BaseException as exc:
        try:
            send_conn.send(("error", f"{type(exc).__name__}:{str(exc)[:240]}"))
        except Exception:
            pass
    finally:
        send_conn.close()


def _scrape_player_profile_isolated(participant_id, attempt_token: str, profile_url: str,
                                    resume_pages: list[dict[str, Any]], stop_before_date):
    """Run profile Chromium outside the worker thread and fence it by job lease."""
    from db.connection import SessionLocal
    from db.models import TennisPlayerRefreshJob
    from .tennis_flashscore import ScrapeResult

    from .player_profiles import profile_refresh_timeout_seconds
    timeout_seconds = profile_refresh_timeout_seconds(stop_before_date)
    context = multiprocessing.get_context("spawn")
    recv_conn, send_conn = context.Pipe(duplex=False)
    process = context.Process(
        target=_player_refresh_scrape_process,
        args=(participant_id, attempt_token, profile_url, resume_pages, stop_before_date, send_conn),
        name=f"flashscore-player-{str(participant_id)[:8]}",
    )
    process.start()
    send_conn.close()
    deadline = time.monotonic() + timeout_seconds
    message = None
    terminate_reason = None
    while process.is_alive() and message is None:
        if recv_conn.poll(0.2):
            try:
                message = recv_conn.recv()
            except EOFError:
                break
        if time.monotonic() >= deadline:
            terminate_reason = "PROFILE_TIMEOUT_PROCESS_TERMINATED"
            break
        # A reclaimed or expired lease must stop the browser promptly. The
        # child also fences every checkpoint, while this check interrupts a
        # navigation that has not reached its next checkpoint yet.
        with SessionLocal() as session:
            owner = session.query(TennisPlayerRefreshJob.participant_id).filter_by(
                participant_id=participant_id, attempt_token=attempt_token, status="running"
            ).filter(TennisPlayerRefreshJob.lease_until > datetime.now(timezone.utc)).first()
        if owner is None:
            terminate_reason = "PLAYER_REFRESH_LEASE_LOST_PROCESS_TERMINATED"
            break
    if terminate_reason:
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
        recv_conn.close()
        return ScrapeResult(
            status="unavailable", url=profile_url, retrieved_at=datetime.now(timezone.utc),
            content_hash=None, reason=terminate_reason,
        )
    if process.is_alive() and message is None:
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
    if not process.is_alive():
        # Reap a normally completed child as well as one stopped above; leaving
        # the process handle unjoined can accumulate zombies under steady load.
        process.join()
    try:
        if message is None and recv_conn.poll(1):
            try:
                message = recv_conn.recv()
            except EOFError:
                message = None
        if message is not None:
            kind, payload = message
            if kind == "result":
                return payload
            return ScrapeResult(
                status="unavailable", url=profile_url, retrieved_at=datetime.now(timezone.utc),
                content_hash=None, reason=str(payload),
            )
    finally:
        recv_conn.close()
    return ScrapeResult(
        status="unavailable", url=profile_url, retrieved_at=datetime.now(timezone.utc),
        content_hash=None, reason=f"PROFILE_WORKER_EXITED_{process.exitcode}",
    )


def update_healthy_file():
    try:
        with open("/tmp/healthy", "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except Exception:
        pass


from .predictions import PredictionCoordinator, PredictionManager

@app.on_event("startup")
async def startup_event():
    logger.info("Web Research Service started.")
    update_healthy_file()

    # Background task to keep /tmp/healthy touched
    async def heartbeat():
        while True:
            update_healthy_file()
            await asyncio.sleep(5)

    asyncio.create_task(heartbeat())

    # Background task for external predictions (every 60 minutes)
    async def periodic_predictions():
        coordinator = PredictionCoordinator()
        # initial delay 30s after startup
        await asyncio.sleep(30)
        while True:
            try:
                logger.info("Running periodic external predictions collection (60m interval)...")
                coordinator.run_cycle()
            except Exception as e:
                logger.error(f"Error in periodic predictions loop: {e}", exc_info=True)
            await asyncio.sleep(3600)

    asyncio.create_task(periodic_predictions())

    async def resume_flashscore_imports():
        while True:
            try:
                from db.connection import SessionLocal
                from db.models import FlashscoreImportJob
                now = datetime.now(timezone.utc)
                with SessionLocal() as session:
                    pending = session.query(FlashscoreImportJob.id).filter(
                        FlashscoreImportJob.next_run_at <= now,
                        (FlashscoreImportJob.status == "queued") |
                        ((FlashscoreImportJob.status == "running") & (FlashscoreImportJob.lease_until <= now)),
                    ).order_by(FlashscoreImportJob.created_at).limit(4).all()
                for (job_id,) in pending:
                    key = str(job_id)
                    if key not in flashscore_import_tasks:
                        flashscore_import_tasks.add(key)
                        asyncio.create_task(_run_flashscore_import(job_id))
            except Exception:
                logger.exception("Failed to resume durable Flashscore imports")
            await asyncio.sleep(10)

    # Flashscore browser work is owned by the dedicated flashscore-worker
    # process. Research remains the HTTP/API host and must not compete for
    # durable jobs when that executor is enabled.
    if os.getenv("FLASHSCORE_EXECUTOR_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        asyncio.create_task(resume_flashscore_imports())


@app.get("/health")
def health() -> Dict[str, Any]:
    """Health check endpoint."""
    update_healthy_file()
    return {
        "status": "healthy",
        "service": "research",
        "enabled": config.enabled,
        "max_pages": config.max_pages,
        "allowlist_count": len(config.allowlist),
        "cache_ttl_seconds": config.cache_ttl_seconds,
        "search_backend": config.search_backend,
        "search_engines": (
            ["searxng", "ddg"]
            if config.search_backend in ("hybrid", "both", "all")
            else [config.search_backend]
        ),
        "searxng_configured": bool(config.searxng_url),
        "persist_to_db": config.persist_to_db,
        "utc_time": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/research", response_model=ResearchPacket)
def execute_research(request: ResearchRequest) -> ResearchPacket:
    """
    Executes web research for event or returns cached packet.
    """
    if not config.enabled:
        raise HTTPException(status_code=503, detail="Web Research is currently disabled in configuration.")

    packet = research_service.execute_research(request)
    return packet


@app.get("/api/research/cache")
def get_cache_info() -> Dict[str, Any]:
    """Returns research cache statistics."""
    return {
        "cache_size": len(research_service.cache._cache),
        "ttl_seconds": research_service.cache.ttl_seconds,
    }


@app.get("/api/research/history")
def get_research_history() -> List[Dict[str, Any]]:
    """Returns recent research executions."""
    return research_service.cache.get_history()


@app.post("/api/predictions/scrape")
def trigger_predictions_scrape() -> Dict[str, Any]:
    """Manually trigger scraping of pre-match predictions across all 9 portals."""
    coordinator = PredictionCoordinator()
    return coordinator.run_cycle()


@app.get("/api/predictions/consensus/{event_id}")
def get_prediction_consensus(event_id: str) -> Dict[str, Any]:
    """Returns consensus summary across all external predictions for an event."""
    manager = PredictionManager()
    summary = manager.get_consensus_for_event(event_id)
    return summary.model_dump()


@app.post("/api/predictions/cleanup")
def cleanup_expired_predictions() -> Dict[str, Any]:
    """Manually cleans up unmatched predictions past 24h TTL."""
    manager = PredictionManager()
    deleted = manager.cleanup_expired_unmatched_predictions()
    return {"status": "ok", "deleted_expired_count": deleted}


def _execute_flashscore_import_job(job_id) -> None:
    from db.connection import SessionLocal
    from db.models import FlashscoreImportJob
    now = datetime.now(timezone.utc)
    token = str(uuid.uuid4())
    with SessionLocal() as session:
        job = session.query(FlashscoreImportJob).filter_by(id=job_id).with_for_update(skip_locked=True).first()
        if job is None or (job.status == "running" and job.lease_until and job.lease_until > now):
            return
        job.status, job.attempt_token = "running", token
        job.attempts += 1
        job.lease_until = now + __import__("datetime").timedelta(minutes=5)
        job.updated_at = now
        session.commit()
        urls = list(job.profile_urls or [])
        progress = dict(job.progress_json or {})
    results = list(progress.get("profiles") or [])
    page_checkpoints = dict(progress.get("page_checkpoints") or {})
    def current_complete(row):
        return row.get("status") == "available" and row.get("parser_version") == PARSER_VERSION
    heartbeat_stop = threading.Event()
    heartbeat = threading.Thread(
        target=_run_flashscore_import_heartbeat,
        args=(job_id, token, heartbeat_stop),
        daemon=True,
    )
    heartbeat.start()
    try:
        # Acquire the shared source slot per profile. Holding it across a whole
        # configured batch starved live point-history and player-preparation work.
        for index, url in enumerate(urls):
            if index < len(results) and current_complete(results[index]):
                continue
            if index:
                time.sleep(config.flashscore_tennis_request_delay_seconds)
            checkpoint = page_checkpoints.get(str(index)) or {}
            scrape_result = _scrape_flashscore_profile_isolated(
                job_id, token, index, url,
                (checkpoint.get("pages") or []) if checkpoint.get("parser_version") == PARSER_VERSION else [],
            )
            # Source serialization ends as soon as the response pages are
            # archived. DB reconciliation can be expensive and must not block
            # live PBP requests from using the shared Flashscore slot.
            matches_imported = FlashscoreTennisImporter._persist_result(
                scrape_result, owner_job_id=job_id, owner_job_token=token
            ) if scrape_result.profile else 0
            profile_result = {
                "url": scrape_result.url,
                "parser_version": PARSER_VERSION,
                "status": scrape_result.status,
                "reason": scrape_result.reason,
                "player_id": scrape_result.profile.source_player_id if scrape_result.profile else None,
                "matches_parsed": len(scrape_result.profile.matches) if scrape_result.profile else 0,
                "matches_imported": matches_imported,
                "matches_skipped": scrape_result.profile.skipped_rows if scrape_result.profile else 0,
                "rows_skipped_by_reason": scrape_result.profile.skipped_row_reasons if scrape_result.profile else {},
                "duplicate_source_rows": scrape_result.profile.duplicate_source_rows if scrape_result.profile else 0,
                "pagination_complete": scrape_result.profile.pagination_complete if scrape_result.profile else False,
                "history_scope": scrape_result.profile.history_scope if scrape_result.profile else "partial",
                "pagination_reason": (scrape_result.profile.pagination_reason if scrape_result.profile
                                      else scrape_result.reason),
                "terminal_probe_count": scrape_result.profile.terminal_probe_count if scrape_result.profile else 0,
                "advertised_event_count": scrape_result.profile.advertised_event_count if scrape_result.profile else None,
                "observed_unique_event_count": scrape_result.profile.observed_unique_event_count if scrape_result.profile else 0,
                "observed_unique_singles_event_count": scrape_result.profile.observed_unique_singles_event_count if scrape_result.profile else 0,
                "advertised_count_matches_singles": (
                    scrape_result.profile.advertised_event_count == scrape_result.profile.observed_unique_singles_event_count
                    if scrape_result.profile and scrape_result.profile.advertised_event_count is not None else None
                ),
                "content_hash": scrape_result.content_hash,
                "archived_path": scrape_result.archived_path,
                "archived_page_paths": list(scrape_result.archived_page_paths),
            }
            while len(results) <= index:
                results.append({})
            results[index] = profile_result
            with SessionLocal() as session:
                job = session.query(FlashscoreImportJob).filter_by(
                    id=job_id, attempt_token=token, status="running"
                ).filter(FlashscoreImportJob.lease_until > datetime.now(timezone.utc)).with_for_update().first()
                if job is None:
                    return
                saved_progress = dict(job.progress_json or {})
                checkpoints = dict(saved_progress.get("page_checkpoints") or {})
                saved_progress.update({"profiles": results,
                    "completed_profiles": sum(current_complete(x) for x in results),
                    "processed_profiles": sum(bool(x) for x in results),
                    "total_profiles": len(urls)})
                if profile_result.get("status") == "available":
                    checkpoints.pop(str(index), None)
                    saved_progress.pop("current_profile_index", None)
                    saved_progress.pop("current_profile_url", None)
                    saved_progress.pop("last_archived_page", None)
                saved_progress["page_checkpoints"] = checkpoints
                job.progress_json = saved_progress
                job.lease_until = datetime.now(timezone.utc) + __import__("datetime").timedelta(minutes=5)
                job.updated_at = datetime.now(timezone.utc)
                session.commit()
        with SessionLocal() as session:
            job = session.query(FlashscoreImportJob).filter_by(
                id=job_id, attempt_token=token, status="running"
            ).filter(FlashscoreImportJob.lease_until > datetime.now(timezone.utc)).with_for_update().first()
            if job:
                complete = len(results) == len(urls) and all(current_complete(x) for x in results)
                job.status = "completed" if complete else "queued"
                job.result_json = {"parser_version": PARSER_VERSION, "profiles": results}
                if complete:
                    job.last_error = None
                    job.next_run_at = datetime.now(timezone.utc)
                saved_progress = dict(job.progress_json or {})
                checkpoints = dict(saved_progress.get("page_checkpoints") or {})
                saved_progress.update({"profiles": results,
                    "completed_profiles": sum(current_complete(x) for x in results),
                    "processed_profiles": sum(bool(x) for x in results),
                    "total_profiles": len(urls)})
                if not complete:
                    failed_index = next((i for i, item in enumerate(results) if not current_complete(item)), 0)
                    saved_progress["current_profile_index"] = failed_index
                    saved_progress["current_profile_url"] = urls[failed_index] if failed_index < len(urls) else None
                    saved_progress["last_archived_page"] = checkpoints.get(str(failed_index))
                else:
                    saved_progress.pop("current_profile_index", None)
                    saved_progress.pop("current_profile_url", None)
                    saved_progress.pop("last_archived_page", None)
                    checkpoints.clear()
                saved_progress["page_checkpoints"] = checkpoints
                job.progress_json = saved_progress
                if not complete:
                    job.last_error = next((x.get("reason") or x.get("status") for x in results
                                           if not current_complete(x)), "PROFILE_IMPORT_INCOMPLETE")
                    # A non-visible/unconfirmed pager will not change within minutes.
                    # Keep it retryable, but do not repeatedly reload ten profiles.
                    retry_seconds = (86400 if job.last_error in {
                                         "PAGINATION_NOT_CONFIRMED",
                                         "pagination_control_missing_or_unknown",
                                         "pagination_button_disabled_without_end_proof",
                                         "source_returned_zero_but_button_remains",
                                         "source_terminal_zero_and_button_hidden",
                                         "source_terminal_zero_and_button_disabled",
                                     }
                                     else min(3600, 30 * (2 ** min(job.attempts, 6))))
                    job.next_run_at = datetime.now(timezone.utc) + __import__("datetime").timedelta(seconds=retry_seconds)
                job.lease_until = None
                job.updated_at = datetime.now(timezone.utc)
                session.commit()
    except Exception as exc:
        logger.exception("Flashscore tennis history import failed job=%s", job_id)
        with SessionLocal() as session:
            job = session.query(FlashscoreImportJob).filter_by(
                id=job_id, attempt_token=token, status="running"
            ).filter(FlashscoreImportJob.lease_until > datetime.now(timezone.utc)).with_for_update().first()
            if job:
                job.status = "queued"
                job.last_error = type(exc).__name__
                job.lease_until = None
                job.next_run_at = datetime.now(timezone.utc) + __import__("datetime").timedelta(seconds=min(3600, 30 * (2 ** min(job.attempts, 6))))
                job.updated_at = datetime.now(timezone.utc)
                session.commit()
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=1)


def _run_flashscore_import_heartbeat(job_id, token: str, stop: threading.Event) -> None:
    """Keep a live profile import leased only while its current attempt owns it."""
    from datetime import timedelta
    from db.connection import SessionLocal
    from db.models import FlashscoreImportJob

    while not stop.wait(30):
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            job = session.query(FlashscoreImportJob).filter_by(
                id=job_id, attempt_token=token, status="running"
            ).filter(FlashscoreImportJob.lease_until > now).with_for_update().first()
            if job is None:
                return
            job.lease_until = now + timedelta(minutes=5)
            job.updated_at = now
            session.commit()


async def _run_flashscore_import(job_id) -> None:
    key = str(job_id)
    try:
        await asyncio.to_thread(_execute_flashscore_import_job, job_id)
    finally:
        flashscore_import_tasks.discard(key)


@app.post("/api/tennis-history/flashscore/import")
async def import_flashscore_tennis_history() -> Dict[str, Any]:
    """Import configured tennis player profiles; no URL is accepted from the caller."""
    if not config.flashscore_tennis_profile_urls:
        return {"status": "disabled", "reason": "FLASHSCORE_TENNIS_PROFILE_URLS is empty", "profiles": []}
    if len(config.flashscore_tennis_profile_urls) > 100:
        raise HTTPException(status_code=400, detail="At most 100 player profile URLs may be configured")
    from db.connection import SessionLocal
    from db.models import FlashscoreImportJob
    import hashlib
    import json
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    urls_hash = hashlib.sha256(json.dumps(config.flashscore_tennis_profile_urls, sort_keys=True).encode()).hexdigest()[:16]
    # A parser/protocol revision must be able to schedule an explicit re-import
    # even if the same profile set already completed in this UTC hour.
    job_key = f"profiles:{PARSER_VERSION}:{hour}:{urls_hash}"
    with SessionLocal() as session:
        job = session.query(FlashscoreImportJob).filter_by(job_key=job_key).with_for_update().first()
        if job is None:
            # A parser-only revision should reparse the archived source pages
            # instead of fetching hundreds of unchanged pages again. The
            # current profile page is still fetched live by the worker, and
            # every archived page is revalidated against its content hash.
            prior = (session.query(FlashscoreImportJob)
                     .filter(FlashscoreImportJob.status == "completed",
                             FlashscoreImportJob.profile_urls == config.flashscore_tennis_profile_urls,
                             FlashscoreImportJob.result_json.is_not(None))
                     .order_by(FlashscoreImportJob.updated_at.desc()).first())
            progress = {}
            if prior and prior.result_json:
                checkpoints = {}
                prior_profiles = list((prior.result_json or {}).get("profiles") or [])
                for index, profile_url in enumerate(config.flashscore_tennis_profile_urls):
                    if index >= len(prior_profiles) or prior_profiles[index].get("url") != profile_url:
                        continue
                    pages = []
                    parser_version = str(prior_profiles[index].get("parser_version") or "")
                    for page_index, path in enumerate(prior_profiles[index].get("archived_page_paths") or [], 1):
                        file_hash = str(path).rsplit("/", 1)[-1].split("-", 1)[0]
                        if (not str(path).startswith("snapshots/research/") or
                            len(file_hash) != 64 or any(char not in "0123456789abcdef" for char in file_hash)):
                            continue
                        pages.append({
                            "page_index": page_index,
                            "archived_path": path,
                            "parser_version": parser_version,
                            "content_hash": f"sha256:{file_hash}",
                        })
                    if pages:
                        checkpoints[str(index)] = {"parser_version": PARSER_VERSION, "pages": pages}
                if checkpoints:
                    progress = {"page_checkpoints": checkpoints,
                                "archive_reuse_source_job_id": str(prior.id)}
            job = FlashscoreImportJob(job_key=job_key, status="queued", profile_urls=config.flashscore_tennis_profile_urls,
                                     progress_json=progress, next_run_at=datetime.now(timezone.utc))
            session.add(job)
            session.commit()
        return {"status": job.status, "job_id": str(job.id), "profile_count": len(config.flashscore_tennis_profile_urls),
                "progress": job.progress_json or {}, "last_error": job.last_error}


@app.get("/api/tennis-history/flashscore/import/status")
def get_flashscore_tennis_import_status() -> Dict[str, Any]:
    from db.connection import SessionLocal
    from db.models import FlashscoreImportJob
    with SessionLocal() as session:
        row = session.query(FlashscoreImportJob).order_by(FlashscoreImportJob.created_at.desc()).first()
        if row is None:
            return {"status": "idle", "job_id": None}
        return {"status": row.status, "job_id": str(row.id), "updated_at": row.updated_at.isoformat(),
                "progress": row.progress_json or {}, "result": row.result_json or {}, "last_error": row.last_error}


class TennisPreparationRequest(BaseModel):
    event_ids: List[uuid.UUID] = Field(..., min_length=1, max_length=1000)


class TennisProfileResolution(BaseModel):
    source_player_id: str = Field(..., min_length=1, max_length=64)


def _set_player_sync_state(participant_id, attempt_token: str | None = None, **values) -> None:
    from db.connection import SessionLocal
    from db.models import TennisPlayerProfile
    from db.models import TennisPlayerRefreshJob

    with SessionLocal() as session:
        if attempt_token and session.query(TennisPlayerRefreshJob).filter_by(
            participant_id=participant_id, attempt_token=attempt_token, status="running"
        ).filter(TennisPlayerRefreshJob.lease_until > datetime.now(timezone.utc)).with_for_update().first() is None:
            return
        profile = session.get(TennisPlayerProfile, participant_id)
        if profile is None:
            return
        for key, value in values.items():
            setattr(profile, key, value)
        session.commit()


def _save_player_identity_link(participant_id, source_player_id: str, status: str, evidence: dict) -> None:
    from db.connection import SessionLocal
    from db.models import TennisPlayerIdentityLink
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        latest = session.query(TennisPlayerIdentityLink).filter_by(
            participant_id=participant_id, source="flashscore", source_player_id=source_player_id
        ).order_by(TennisPlayerIdentityLink.available_at.desc(), TennisPlayerIdentityLink.id.desc()).first()
        if latest and latest.status == status and latest.evidence_json == evidence:
            return
        session.add(TennisPlayerIdentityLink(participant_id=participant_id, source="flashscore",
            source_player_id=source_player_id, status=status, evidence_json=evidence, available_at=now))
        session.commit()


def _finish_player_refresh_job(participant_id, attempt_token: str, status: str, error: str | None, progress: dict | None = None) -> bool:
    from db.connection import SessionLocal
    from db.models import TennisPlayerRefreshJob

    with SessionLocal() as session:
        job = session.query(TennisPlayerRefreshJob).filter_by(participant_id=participant_id, attempt_token=attempt_token, status="running").filter(
            TennisPlayerRefreshJob.lease_until > datetime.now(timezone.utc)
        ).with_for_update().first()
        if job is None:
            return False
        now = datetime.now(timezone.utc)
        job.status = status
        if status != "running":
            job.lease_until = None
            job.attempt_token = None
        job.last_error = error[:128] if error else None
        job.progress_json = progress or {"stage": status}
        job.completed_at = now if status in {"completed", "waiting_confirmation", "not_found"} else None
        if status == "running":
            from datetime import timedelta
            job.lease_until = now + timedelta(minutes=5)
        elif status == "retry":
            from datetime import timedelta
            if error == "SEARCH_COMPLETED_NO_MATCH":
                delay = timedelta(hours=6)
            elif error and ("403" in error or "429" in error or "BLOCKED" in error):
                delay = timedelta(minutes=15)
            else:
                delay = timedelta(minutes=min(360, 5 * (2 ** min(max(job.attempts - 1, 0), 6))))
            job.next_run_at = now + delay
        else:
            job.next_run_at = now
        job.updated_at = now
        session.commit()
        return True


def _refresh_job_heartbeat(participant_id, attempt_token: str, stop: threading.Event) -> None:
    from datetime import timedelta
    from db.connection import SessionLocal
    from db.models import TennisPlayerRefreshJob
    while not stop.wait(45):
        with SessionLocal() as session:
            job = session.query(TennisPlayerRefreshJob).filter_by(
                participant_id=participant_id, attempt_token=attempt_token, status="running"
            ).filter(TennisPlayerRefreshJob.lease_until > datetime.now(timezone.utc)).with_for_update().first()
            if job is None:
                return
            job.lease_until = datetime.now(timezone.utc) + timedelta(minutes=5)
            job.updated_at = datetime.now(timezone.utc)
            session.commit()


def _enqueue_candidate_profile_import(profile_url: str, source_player_id: str) -> str:
    """Queue an unlinked profile's history without assigning its metrics to a person."""
    from datetime import timedelta
    from db.connection import SessionLocal
    from db.models import FlashscoreImportJob
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    now = datetime.now(timezone.utc)
    job_key = f"candidate-profile:{source_player_id}:{PARSER_VERSION}:{now:%Y%m%d}"
    with SessionLocal() as session:
        active_jobs = session.query(FlashscoreImportJob).filter(
            FlashscoreImportJob.status.in_(("queued", "running"))
        ).order_by(FlashscoreImportJob.created_at.desc()).all()
        for job in active_jobs:
            if profile_url in (job.profile_urls or []):
                return str(job.id)

        recent_completed = session.query(FlashscoreImportJob).filter(
            FlashscoreImportJob.status == "completed",
            FlashscoreImportJob.updated_at >= now - timedelta(days=30),
        ).order_by(FlashscoreImportJob.updated_at.desc()).all()
        for job in recent_completed:
            if profile_url not in (job.profile_urls or []):
                continue
            result_rows = (job.result_json or {}).get("profiles") or []
            result = next((row for row in result_rows if row.get("url") == profile_url), None)
            if result and result.get("status") == "available" and result.get("pagination_complete"):
                return str(job.id)

        job = session.query(FlashscoreImportJob).filter_by(job_key=job_key).with_for_update().first()
        if job is None:
            session.execute(pg_insert(FlashscoreImportJob).values(
                job_key=job_key,
                status="queued",
                profile_urls=[profile_url],
                progress_json={"reason": "unlinked_candidate_profile", "source_player_id": source_player_id},
                next_run_at=now,
            ).on_conflict_do_nothing(index_elements=[FlashscoreImportJob.job_key]))
            session.commit()
            job = session.query(FlashscoreImportJob).filter_by(job_key=job_key).first()
        return str(job.id)


def _completed_profile_archive_checkpoint(session, profile_url: str) -> list[dict[str, Any]]:
    """Reuse the latest completed profile archive after identity confirmation."""
    from db.models import FlashscoreImportJob

    jobs = session.query(FlashscoreImportJob).filter(
        FlashscoreImportJob.status == "completed"
    ).order_by(FlashscoreImportJob.updated_at.desc()).limit(100).all()
    for job in jobs:
        profile_result = next((row for row in (job.result_json or {}).get("profiles", [])
                               if row.get("url") == profile_url and row.get("status") == "available"
                               and row.get("pagination_complete")), None)
        if not profile_result:
            continue
        parser_version = str(profile_result.get("parser_version") or
                             (job.result_json or {}).get("parser_version") or "")
        pages = []
        for page_index, path in enumerate(profile_result.get("archived_page_paths") or [], 1):
            digest = str(path).rsplit("/", 1)[-1].split("-", 1)[0]
            if (not str(path).startswith("snapshots/research/") or len(digest) != 64 or
                    any(char not in "0123456789abcdef" for char in digest)):
                return []
            pages.append({
                "page_index": page_index,
                "archived_path": path,
                "parser_version": parser_version,
                "content_hash": f"sha256:{digest}",
            })
        return pages
    return []


def _prepare_one_player(participant_id, participant_name: str, attempt_token: str) -> None:
    from db.connection import SessionLocal
    from db.models import Participant, TennisPlayerMetricSnapshot, TennisPlayerProfile
    from tennis_adapter.player_metrics import digest_metrics, empty_metrics
    from db.tennis_confirmations import upsert_manual_confirmation
    from .player_profiles import seed_profile_from_archive

    importer = FlashscoreTennisImporter()
    had_cached_metrics = False
    incremental_after = None
    full_scan = True
    resume_after_page = 0
    job_status = "failed"
    job_error = "REFRESH_ABORTED"
    job_progress = {"stage": "starting"}
    stop_heartbeat = threading.Event()
    heartbeat = threading.Thread(target=_refresh_job_heartbeat, args=(participant_id, attempt_token, stop_heartbeat), daemon=True)
    heartbeat.start()
    try:
        with SessionLocal() as session:
            from db.models import TennisPlayerRefreshJob
            owner = session.query(TennisPlayerRefreshJob).filter_by(participant_id=participant_id, attempt_token=attempt_token, status="running").filter(TennisPlayerRefreshJob.lease_until > datetime.now(timezone.utc)).with_for_update().first()
            if owner is None:
                return
            previous_progress = dict(owner.progress_json or {})
            checkpoint = previous_progress.get("page_checkpoint") or {}
            resume_pages = (checkpoint.get("pages") or []) if checkpoint.get("parser_version") == PARSER_VERSION else []
            profile = session.get(TennisPlayerProfile, participant_id)
            participant = session.get(Participant, participant_id)
            # Capture scalar identity before leaving this Session. Profile
            # seeding may commit and expire ORM instances; candidate evidence
            # is written in a later Session below.
            canonical_participant_name = (
                str(participant.canonical_name) if participant is not None else str(participant_name)
            )
            from db.models import TennisPlayerIdentityLink
            latest_identity = session.query(TennisPlayerIdentityLink).filter_by(
                participant_id=participant_id, source="flashscore"
            ).order_by(TennisPlayerIdentityLink.available_at.desc(), TennisPlayerIdentityLink.id.desc()).first()
            confirmed_source_id = (
                latest_identity.source_player_id
                if latest_identity is not None and latest_identity.status == "confirmed"
                else None
            )
            if profile is not None and participant is not None and not profile.metrics_version:
                seed_profile_from_archive(session, participant, profile)
                session.commit()
            identities = (profile.source_identities or {}) if profile else {}
            rejected_source_ids = {
                row.source_player_id for row in session.query(TennisPlayerIdentityLink).filter_by(
                    participant_id=participant_id, source="flashscore", status="rejected"
                ).all()
            }
            profile_url = (
                profile.profile_url
                if profile and confirmed_source_id and identities.get("flashscore") == confirmed_source_id
                else None
            )
            had_cached_metrics = bool(profile and profile.metrics_version > 0)
            if profile and profile.last_full_history_scan_at:
                from datetime import timedelta
                full_scan = datetime.now(timezone.utc) - profile.last_full_history_scan_at >= timedelta(days=30)
                if not full_scan:
                    incremental_after = (profile.source_watermark or datetime.now(timezone.utc)) - timedelta(days=30)
            if profile_url and not resume_pages:
                resume_pages = _completed_profile_archive_checkpoint(session, profile_url)
        if not profile_url:
            job_progress = {"stage": "resolving_profile"}
            resolved = importer.resolve_player_profile(
                participant_name,
                excluded_source_ids=rejected_source_ids,
                source_access=lambda: _flashscore_source_access("participant"),
            )
            from .tennis_flashscore import is_auto_confirmable_player_match
            exact_candidates = [candidate for candidate in (resolved.get("candidates") or [])
                                if is_auto_confirmable_player_match(
                                    participant_name, str(candidate.get("name") or ""))]
            if resolved.get("status") == "ambiguous" and len(exact_candidates) == 1:
                exact = exact_candidates[0]
                resolved = {"status": "matched", "candidate": exact,
                            "profile_url": exact.get("profile_url"), "candidates": [exact]}
            if resolved.get("status") != "matched":
                status = "stale" if had_cached_metrics else (
                    "identity_confirmation_required" if resolved.get("status") == "ambiguous"
                    else resolved.get("status", "not_found")
                )
                job_error = resolved.get("reason") or resolved.get("status", "not_found")
                if resolved.get("status") == "ambiguous":
                    job_status = "waiting_confirmation"
                    job_progress = {"stage": "identity_confirmation_required", "candidates": resolved.get("candidates", [])}
                elif resolved.get("status") == "not_found":
                    job_status = "retry"
                    job_error = "NO_ALTERNATIVE_PROFILE_DATA" if rejected_source_ids else "SEARCH_COMPLETED_NO_MATCH"
                    job_progress = {"stage": "confirmed_not_found", "retry_after_hours": 6,
                                    "excluded_rejected_candidates": len(rejected_source_ids)}
                else:
                    job_status = "retry"
                    job_error = resolved.get("reason") or resolved.get("status", "SOURCE_UNAVAILABLE")
                    job_progress = {"stage": "source_retry", "reason": job_error}
                with SessionLocal() as session:
                    from db.models import TennisPlayerRefreshJob
                    owner = session.query(TennisPlayerRefreshJob).filter_by(participant_id=participant_id, attempt_token=attempt_token, status="running").filter(TennisPlayerRefreshJob.lease_until > datetime.now(timezone.utc)).with_for_update().first()
                    if owner is None:
                        return
                    profile = session.get(TennisPlayerProfile, participant_id)
                    if profile:
                        profile.sync_status = status
                        profile.sync_error = resolved.get("reason") or resolved.get("status", "not_found").upper()
                        if resolved.get("candidates"):
                            identities = dict(profile.source_identities or {})
                            identities["flashscore_candidates"] = resolved["candidates"]
                            profile.source_identities = identities
                            for candidate in resolved["candidates"]:
                                source_player_id = str(candidate.get("source_player_id") or "")
                                if source_player_id:
                                    upsert_manual_confirmation(session, kind="player_profile",
                                        subject_id=str(participant_id), candidate_id=source_player_id,
                                        evidence={"participant_name": canonical_participant_name,
                                                  "candidate": candidate, "query_name": participant_name})
                        session.commit()
                return
            profile_url = resolved["profile_url"]
            source_id = resolved["candidate"]["source_player_id"]
            candidate_name = str(resolved["candidate"].get("name") or "")
            from .tennis_flashscore import is_auto_confirmable_player_match, is_exact_player_name_match
            if is_auto_confirmable_player_match(participant_name, candidate_name):
                confirmation_method = (
                    "automatic_exact_full_name" if is_exact_player_name_match(participant_name, candidate_name)
                    else "automatic_full_surname_initial"
                )
                now = datetime.now(timezone.utc)
                with SessionLocal() as session:
                    from db.models import TennisPlayerIdentityLink, TennisPlayerRefreshJob
                    owner = session.query(TennisPlayerRefreshJob).filter_by(
                        participant_id=participant_id, attempt_token=attempt_token, status="running"
                    ).filter(TennisPlayerRefreshJob.lease_until > now).with_for_update().first()
                    profile = session.get(TennisPlayerProfile, participant_id, with_for_update=True)
                    if owner is None or profile is None:
                        return
                    identities = dict(profile.source_identities or {})
                    identities["flashscore"] = source_id
                    identities.pop("flashscore_candidates", None)
                    identities.pop("flashscore_candidate_import_job_id", None)
                    profile.profile_url = profile_url
                    profile.source_identities = identities
                    profile.sync_status = "running"
                    profile.sync_error = None
                    session.add(TennisPlayerIdentityLink(
                        participant_id=participant_id, source="flashscore", source_player_id=source_id,
                        status="confirmed", evidence_json={
                            "method": confirmation_method,
                            "participant_name": participant_name,
                            "candidate_name": candidate_name,
                            "profile_url": profile_url,
                        },
                        available_at=now,
                    ))
                    session.commit()
                logger.info("Automatically confirmed Flashscore profile match method=%s participant=%s source_player=%s",
                            confirmation_method, participant_id, source_id)
            else:
                candidate_import_job_id = _enqueue_candidate_profile_import(profile_url, source_id)
                _save_player_identity_link(participant_id, source_id, "candidate", {
                    "method": "unique_flashscore_search_name_candidate",
                    "query_name": participant_name,
                    "candidate_name": candidate_name,
                    "profile_url": profile_url,
                    "context_confirmed": False,
                })
                _set_player_sync_state(
                    participant_id,
                    attempt_token=attempt_token,
                    profile_url=None,
                    sync_status="identity_confirmation_required",
                    sync_error="FLASHScore_PROFILE_REQUIRES_CONFIRMATION",
                    source_identities={
                        "flashscore_candidates": [resolved["candidate"]],
                        "flashscore_candidate_import_job_id": candidate_import_job_id,
                    },
                )
                with SessionLocal() as session:
                    upsert_manual_confirmation(session, kind="player_profile", subject_id=str(participant_id),
                        candidate_id=str(source_id), evidence={"participant_name": participant_name,
                        "candidate": resolved["candidate"], "query_name": participant_name,
                        "candidate_import_job_id": candidate_import_job_id})
                    session.commit()
                job_status = "waiting_confirmation"
                job_error = "IDENTITY_CONFIRMATION_REQUIRED"
                job_progress = {
                    "stage": "identity_confirmation_required",
                    "candidate": resolved["candidate"],
                    "candidate_import_job_id": candidate_import_job_id,
                }
                return
        job_progress = {"stage": "loading_history", "profile_url": profile_url,
                        "resume_pages": len(resume_pages),
                        "page_checkpoint": {"parser_version": PARSER_VERSION, "pages": resume_pages}}
        if not _finish_player_refresh_job(participant_id, attempt_token, "running", None, job_progress):
            return
        result = _scrape_player_profile_isolated(
            participant_id, attempt_token, profile_url, resume_pages, incremental_after
        )
        if result.profile is not None:
            with SessionLocal() as session:
                from db.models import TennisPlayerRefreshJob
                owner = session.query(TennisPlayerRefreshJob).filter_by(participant_id=participant_id, attempt_token=attempt_token, status="running").filter(TennisPlayerRefreshJob.lease_until > datetime.now(timezone.utc)).first()
                if owner is None:
                    return
            importer._persist_result(result, owner_participant_id=participant_id, owner_job_token=attempt_token)
            job_progress = {
                "stage": "persisting_history",
                "matches_parsed": len(result.profile.matches),
                "pagination_complete": result.profile.pagination_complete,
                "history_scope": result.profile.history_scope,
            }
            if result.profile.pagination_complete:
                job_status, job_error = "completed", None
            else:
                job_status = "retry"
                job_error = "HISTORY_PAGINATION_INCOMPLETE"
        else:
            job_status = "retry"
            job_error = result.reason or result.status
        throttle_signal = f"{getattr(result, 'status', '')} {getattr(result, 'reason', '')}".upper()
        if any(marker in throttle_signal for marker in ("429", "403", "BLOCKED", "RATE_LIMIT")):
            _record_flashscore_throttle()
        with SessionLocal() as session:
            from db.models import TennisPlayerRefreshJob
            owner = session.query(TennisPlayerRefreshJob).filter_by(participant_id=participant_id, attempt_token=attempt_token, status="running").filter(TennisPlayerRefreshJob.lease_until > datetime.now(timezone.utc)).with_for_update().first()
            if owner is None:
                return
            profile = session.get(TennisPlayerProfile, participant_id)
            if profile is None:
                return
            has_metrics = bool(profile.metrics_json)
            if result.profile and result.profile.pagination_complete and not result.profile.matches and not has_metrics:
                metrics = empty_metrics()
                metrics["coverage"]["match_history"] = "confirmed_empty"
                profile.metrics_version = 1
                profile.metrics_json = metrics
                profile.history_digest = digest_metrics(metrics)
                profile.history_match_count = 0
                profile.metrics_available_at = result.retrieved_at
                profile.sync_status = "empty"
                session.add(TennisPlayerMetricSnapshot(
                    participant_id=participant_id,
                    metrics_version=1,
                    available_at=result.retrieved_at,
                    source_watermark=None,
                    history_digest=profile.history_digest,
                    metrics_json=metrics,
                ))
            elif result.profile is not None:
                if result.profile.pagination_complete:
                    profile.sync_status = "available"
                    profile.sync_error = None
                elif had_cached_metrics:
                    profile.sync_status = "stale"
                    profile.sync_error = "HISTORY_PAGINATION_INCOMPLETE"
                else:
                    profile.sync_status = "partial"
                    profile.sync_error = "HISTORY_PAGINATION_INCOMPLETE"
            elif has_metrics:
                profile.sync_status = "stale"
                profile.sync_error = result.reason or result.status
            else:
                profile.sync_status = result.status
                profile.sync_error = result.reason or result.status
            profile.last_attempt_at = result.retrieved_at
            if result.profile is not None:
                profile.last_success_at = result.retrieved_at
                if result.profile.history_scope == "full_accessible" and full_scan:
                    profile.last_full_history_scan_at = result.retrieved_at
            session.commit()
    except Exception as exc:
        logger.exception("Tennis player preparation failed participant=%s", participant_id)
        job_status = "retry"
        job_error = type(exc).__name__
        job_progress = {"stage": "source_retry", "error": job_error}
        with SessionLocal() as session:
            from db.models import TennisPlayerRefreshJob
            owner = session.query(TennisPlayerRefreshJob).filter_by(participant_id=participant_id, attempt_token=attempt_token, status="running").filter(TennisPlayerRefreshJob.lease_until > datetime.now(timezone.utc)).with_for_update().first()
            if owner is None:
                return
            profile = session.get(TennisPlayerProfile, participant_id)
            if profile:
                profile.sync_status = "stale" if had_cached_metrics else "unavailable"
                profile.sync_error = type(exc).__name__
                session.commit()
    finally:
        stop_heartbeat.set()
        heartbeat.join(timeout=1)
        _finish_player_refresh_job(participant_id, attempt_token, job_status, job_error, job_progress)


@app.post("/api/tennis-history/prepare")
async def prepare_tennis_event_players(request: TennisPreparationRequest) -> Dict[str, Any]:
    """Resolve and refresh both participant profiles before odds-free forecasting."""
    from datetime import timedelta
    from db.connection import SessionLocal
    from db.models import Event, Participant, TennisPlayerProfile, TennisPlayerRefreshJob
    from .player_profiles import enqueue_player_refresh, resolve_participant
    from .tennis_flashscore import is_doubles_player_display

    now = datetime.now(timezone.utc)
    outcomes: dict[str, Any] = {}
    with SessionLocal() as session:
        events = session.query(Event).filter(Event.id.in_(request.event_ids), Event.sport_code == "tennis").all()
        for event in events:
            sides = []
            for participant_id, name in (
                (event.participant_a_id, event.participant_a_name),
                (event.participant_b_id, event.participant_b_name),
            ):
                if is_doubles_player_display(name):
                    sides.append({"status": "unsupported_doubles", "reason": "SINGLES_ONLY", "name": name})
                    continue
                participant = session.get(Participant, participant_id) if participant_id else resolve_participant(session, name)
                if participant is None:
                    sides.append({"status": "participant_unlinked", "name": name})
                    continue
                profile = session.get(TennisPlayerProfile, participant.id)
                if profile is None:
                    profile = TennisPlayerProfile(participant_id=participant.id, sync_status="pending")
                    session.add(profile)
                    session.flush()
                retry_wait = timedelta(hours=6) if profile.sync_status in {"not_found", "ambiguous"} else timedelta(minutes=15)
                attempt_recent = profile.last_attempt_at is not None and profile.last_attempt_at > now - retry_wait
                needs_refresh = (
                    not attempt_recent
                    and profile.sync_status != "identity_confirmation_required"
                    and (
                        profile.sync_status == "partial"
                        or profile.last_success_at is None
                        or profile.last_success_at < event.first_seen_at
                        or profile.last_success_at < now - timedelta(hours=24)
                    )
                )
                job = session.get(TennisPlayerRefreshJob, participant.id, with_for_update=True)
                job_active = bool(job and job.status in {"queued", "running", "waiting_confirmation"}
                                  and (job.status in {"queued", "waiting_confirmation"}
                                       or (job.lease_until and job.lease_until > now)))
                if needs_refresh and not attempt_recent and not job_active:
                    refresh_job = enqueue_player_refresh(
                        session, participant.id,
                        "new_event" if profile.last_success_at is None else "event_refresh",
                    )
                    if refresh_job.status != "waiting_confirmation":
                        profile.sync_status = "running"
                        profile.last_attempt_at = now
                elif job_active and job.status != "waiting_confirmation":
                    profile.sync_status = "running"
                key = str(participant.id)
                sides.append({
                    "participant_id": key,
                    "name": participant.canonical_name,
                    "status": profile.sync_status,
                    "history_match_count": profile.history_match_count,
                    "metrics_version": profile.metrics_version,
                    "metrics_available_at": profile.metrics_available_at.isoformat() if profile.metrics_available_at else None,
                    "sync_error": profile.sync_error,
                    # Partial first imports are not enough to claim we fetched the player's history.
                    # Existing cached metrics remain usable when a later refresh is incomplete.
                    "ready": profile.sync_status in {"available", "empty", "stale"} and profile.metrics_version > 0,
                })
            ready = len(sides) == 2 and all(side.get("ready", False) for side in sides)
            outcomes[str(event.id)] = {"ready": ready, "players": sides}
        session.commit()
    return {"events": outcomes, "ready_event_ids": [event_id for event_id, value in outcomes.items() if value["ready"]]}


@app.get("/api/tennis-history/players/{participant_id}")
def get_tennis_player_profile(participant_id: uuid.UUID) -> Dict[str, Any]:
    from db.connection import SessionLocal
    from db.models import Participant, TennisPlayerProfile, TennisPlayerRefreshJob

    with SessionLocal() as session:
        participant = session.get(Participant, participant_id)
        profile = session.get(TennisPlayerProfile, participant_id)
        job = session.get(TennisPlayerRefreshJob, participant_id)
        if participant is None:
            raise HTTPException(status_code=404, detail="Tennis participant not found")
        if profile is None:
            return {"participant_id": str(participant_id), "canonical_name": participant.canonical_name, "status": "pending", "metrics": None}
        candidate_import_id = (profile.source_identities or {}).get("flashscore_candidate_import_job_id")
        candidate_import_job = None
        if candidate_import_id:
            try:
                from db.models import FlashscoreImportJob
                candidate_import_job = session.get(FlashscoreImportJob, uuid.UUID(str(candidate_import_id)))
            except (TypeError, ValueError):
                candidate_import_job = None
        return {
            "participant_id": str(participant_id),
            "canonical_name": participant.canonical_name,
            "canonical_player_id": str(profile.canonical_player_id) if profile.canonical_player_id else None,
            "source_identities": profile.source_identities,
            "profile_url": profile.profile_url,
            "status": profile.sync_status,
            "error": profile.sync_error,
            "last_attempt_at": profile.last_attempt_at.isoformat() if profile.last_attempt_at else None,
            "last_success_at": profile.last_success_at.isoformat() if profile.last_success_at else None,
            "source_watermark": profile.source_watermark.isoformat() if profile.source_watermark else None,
            "last_full_history_scan_at": profile.last_full_history_scan_at.isoformat() if profile.last_full_history_scan_at else None,
            "metrics_version": profile.metrics_version,
            "metrics_available_at": profile.metrics_available_at.isoformat() if profile.metrics_available_at else None,
            "history_match_count": profile.history_match_count,
            "history_digest": profile.history_digest,
            "metrics": profile.metrics_json,
            "refresh_job": {
                "status": job.status,
                "reason": job.requested_reason,
                "attempts": job.attempts,
                "lease_until": job.lease_until.isoformat() if job.lease_until else None,
                "progress": job.progress_json,
                "error": job.last_error,
            } if job else None,
            "candidate_profile_import": {
                "job_id": str(candidate_import_job.id),
                "status": candidate_import_job.status,
                "progress": candidate_import_job.progress_json,
                "result": candidate_import_job.result_json,
                "last_error": candidate_import_job.last_error,
            } if candidate_import_job else None,
        }


@app.post("/api/tennis-history/players/{participant_id}/resolve")
def resolve_tennis_player_profile(participant_id: uuid.UUID, payload: TennisProfileResolution, http_request: Request) -> Dict[str, Any]:
    """Manually bind one previously saved ambiguous Flashscore candidate."""
    import hmac
    internal_token = os.getenv("ADMIN_INTERNAL_TOKEN", "")
    if not internal_token:
        raise HTTPException(status_code=503, detail="Admin service authentication is not configured")
    if not hmac.compare_digest(http_request.headers.get("X-Admin-Internal-Token", ""), internal_token):
        raise HTTPException(status_code=401, detail="Admin service token required")
    from db.connection import SessionLocal
    from db.models import Participant, TennisPlayerIdentityLink, TennisPlayerProfile
    from .player_profiles import enqueue_player_refresh

    with SessionLocal() as session:
        participant = session.get(Participant, participant_id)
        profile = session.get(TennisPlayerProfile, participant_id, with_for_update=True)
        if participant is None or profile is None:
            raise HTTPException(status_code=404, detail="Tennis participant profile not found")
        identities = dict(profile.source_identities or {})
        candidates = identities.get("flashscore_candidates") or []
        candidate = next((row for row in candidates if row.get("source_player_id") == payload.source_player_id), None)
        if candidate is None or not candidate.get("profile_url"):
            raise HTTPException(status_code=409, detail="Source player is not a saved candidate")
        profile.profile_url = candidate["profile_url"]
        identities["flashscore"] = payload.source_player_id
        identities.pop("flashscore_candidates", None)
        profile.source_identities = identities
        now = datetime.now(timezone.utc)
        prior_links = session.query(TennisPlayerIdentityLink).filter_by(
            participant_id=participant_id, source="flashscore"
        ).order_by(TennisPlayerIdentityLink.available_at, TennisPlayerIdentityLink.id).all()
        latest_by_source_id = {row.source_player_id: row for row in prior_links}
        for prior_id, prior in latest_by_source_id.items():
            if prior.status == "confirmed" and prior_id != payload.source_player_id:
                session.add(TennisPlayerIdentityLink(participant_id=participant_id, source="flashscore",
                    source_player_id=prior_id, status="revoked",
                    evidence_json={"method": "manual_reassignment", "replaced_by": payload.source_player_id},
                    available_at=now))
        # Make this the unambiguous final decision even when multiple prior IDs
        # are revoked in the same transaction and therefore share a timestamp.
        confirmed_at = now + timedelta(microseconds=1)
        session.add(TennisPlayerIdentityLink(participant_id=participant_id, source="flashscore",
            source_player_id=payload.source_player_id, status="confirmed",
            evidence_json={"method": "manual_confirmation", "profile_url": candidate["profile_url"],
                           "participant_name": participant.canonical_name}, available_at=confirmed_at))
        profile.sync_status = "pending"
        profile.sync_error = None
        enqueue_player_refresh(session, participant_id, "manual_identity_resolution")
        session.commit()
        return {"participant_id": str(participant_id), "source_player_id": payload.source_player_id, "status": "queued"}


@app.post("/api/tennis-history/refresh-due")
async def refresh_due_tennis_players(limit: int = 500) -> Dict[str, Any]:
    """Queue tracked profiles due by the daily timer or a newly finished match."""
    from datetime import timedelta
    from sqlalchemy import exists, func, or_, select
    from db.connection import SessionLocal
    from db.models import Event, Participant, TennisPlayerProfile, TennisPlayerRefreshJob
    from .player_profiles import enqueue_player_refresh

    limit = max(1, min(limit, 1000))
    now = datetime.now(timezone.utc)
    due_before = now - timedelta(hours=24)
    retry_before = now - timedelta(minutes=15)
    queued_count = 0
    due_count = 0
    newly_finished = exists(select(1).where(
        Event.sport_code == "tennis",
        func.lower(Event.status).in_(("finished", "ended", "completed")),
        Event.finished_at.is_not(None),
        or_(
            Event.participant_a_id == TennisPlayerProfile.participant_id,
            Event.participant_b_id == TennisPlayerProfile.participant_id,
        ),
        or_(
            TennisPlayerProfile.last_success_at.is_(None),
            Event.finished_at > TennisPlayerProfile.last_success_at,
        ),
    ))
    with SessionLocal() as session:
        rows = session.execute(
            select(TennisPlayerProfile, Participant)
            .join(Participant, Participant.id == TennisPlayerProfile.participant_id)
            .where(
                Participant.sport_code == "tennis",
                Participant.canonical_name.not_like("%/%"),
                TennisPlayerProfile.metrics_version > 0,
                or_(
                    TennisPlayerProfile.last_success_at.is_(None),
                    TennisPlayerProfile.last_success_at <= due_before,
                    newly_finished,
                ),
                or_(TennisPlayerProfile.last_attempt_at.is_(None), TennisPlayerProfile.last_attempt_at <= retry_before),
            )
            .order_by(TennisPlayerProfile.last_success_at.asc().nullsfirst())
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        due_count = len(rows)
        for profile, participant in rows:
            job = session.get(TennisPlayerRefreshJob, participant.id, with_for_update=True)
            if job and job.status in {"queued", "running", "waiting_confirmation"} and (
                    job.status in {"queued", "waiting_confirmation"} or (job.lease_until and job.lease_until > now)):
                continue
            refresh_job = enqueue_player_refresh(session, participant.id, "daily_or_match_finished")
            if refresh_job.status != "waiting_confirmation":
                profile.sync_status = "running"
                profile.last_attempt_at = now
                queued_count += 1
        session.commit()
    return {"status": "queued", "due_count": due_count, "scheduled_count": queued_count}


def _tennis_player_dispatch_priority(participant_id_column, live_ids: set[Any], eligible_ids: set[Any]):
    """Keep stale archive backlog from consuming the live/prematch profile slots."""
    from sqlalchemy import case

    return case(
        (participant_id_column.in_(live_ids), 0),
        (participant_id_column.in_(eligible_ids), 1),
        else_=2,
    )


def _tennis_stale_running_job_filter(job_model, eligible_ids: set[Any], now: datetime):
    from sqlalchemy import and_

    columns = job_model.__table__.c if hasattr(job_model, "__table__") else job_model.c
    return and_(
        columns.status == "running",
        columns.lease_until > now,
        ~columns.participant_id.in_(eligible_ids),
    )


def _profile_status_after_preemption(profile) -> str:
    """Keep a revoked archive refresh from leaving the cached profile stuck as running."""
    return "stale" if int(getattr(profile, "metrics_version", 0) or 0) > 0 else "pending"


@app.post("/api/tennis-history/jobs/dispatch")
async def dispatch_tennis_player_refresh_jobs(limit: int = 10) -> Dict[str, Any]:
    """Claim durable profile jobs and hand them to the isolated scraper executor."""
    from datetime import timedelta
    from sqlalchemy import func, or_, select, text
    from db.connection import SessionLocal
    from db.models import (
        Event, ManualConfirmationDecision, ManualConfirmationRequest, Participant,
        TennisPlayerIdentityLink, TennisPlayerProfile, TennisPlayerRefreshJob,
    )
    from .player_profiles import enqueue_player_refresh
    from .tennis_flashscore import (
        is_auto_confirmable_player_match, is_doubles_player_display, is_exact_player_name_match,
    )

    limit = max(1, min(limit, 50))
    now = datetime.now(timezone.utc)
    claimed: list[tuple[Any, str, str]] = []
    skipped_doubles = 0
    auto_confirmed_exact = 0
    with SessionLocal() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('neurobet:tennis-player-dispatch'))"))
        pending_confirmations = session.query(ManualConfirmationRequest).filter_by(
            kind="player_profile", status="pending"
        ).order_by(ManualConfirmationRequest.created_at, ManualConfirmationRequest.id).with_for_update(
            skip_locked=True
        ).all()
        by_subject: dict[str, list[Any]] = {}
        for confirmation in pending_confirmations:
            by_subject.setdefault(confirmation.subject_id, []).append(confirmation)
        for subject_id, confirmations in by_subject.items():
            try:
                participant_id = uuid.UUID(subject_id)
            except ValueError:
                continue
            participant = session.get(Participant, participant_id)
            profile = session.get(TennisPlayerProfile, participant_id, with_for_update=True)
            if participant is None:
                continue
            if profile is None:
                profile = TennisPlayerProfile(participant_id=participant_id)
                session.add(profile)
            exact = [row for row in confirmations if is_auto_confirmable_player_match(
                participant.canonical_name,
                str((row.evidence_json or {}).get("candidate", {}).get("name") or ""),
            )]
            if len(exact) != 1:
                continue
            confirmation = exact[0]
            candidate = (confirmation.evidence_json or {}).get("candidate") or {}
            source_player_id = str(candidate.get("source_player_id") or confirmation.candidate_id)
            profile_url = candidate.get("profile_url")
            if not source_player_id or not profile_url:
                continue
            confirmation_method = (
                "automatic_exact_full_name"
                if is_exact_player_name_match(
                    participant.canonical_name,
                    str((confirmation.evidence_json or {}).get("candidate", {}).get("name") or ""),
                )
                else "automatic_full_surname_initial"
            )
            identity_history = session.query(TennisPlayerIdentityLink).filter_by(
                participant_id=participant_id, source="flashscore"
            ).order_by(TennisPlayerIdentityLink.available_at.desc(), TennisPlayerIdentityLink.id.desc()).all()
            latest_by_source: dict[str, Any] = {}
            for link in identity_history:
                latest_by_source.setdefault(link.source_player_id, link)
            if any(link.status == "rejected" and source_id == source_player_id
                   for source_id, link in latest_by_source.items()):
                continue
            if any(link.status == "confirmed" and source_id != source_player_id
                   for source_id, link in latest_by_source.items()):
                continue

            decision_time = datetime.now(timezone.utc)
            identities = dict(profile.source_identities or {})
            identities["flashscore"] = source_player_id
            identities.pop("flashscore_candidates", None)
            identities.pop("flashscore_candidate_import_job_id", None)
            profile.profile_url = profile_url
            profile.source_identities = identities
            profile.sync_status = "pending"
            profile.sync_error = None
            latest_candidate_link = latest_by_source.get(source_player_id)
            if not latest_candidate_link or latest_candidate_link.status != "confirmed":
                session.add(TennisPlayerIdentityLink(
                    participant_id=participant_id, source="flashscore", source_player_id=source_player_id,
                    status="confirmed", evidence_json={
                        "method": confirmation_method,
                        "participant_name": participant.canonical_name,
                        "candidate_name": candidate.get("name"), "profile_url": profile_url,
                    }, available_at=decision_time,
                ))
            prior_version = confirmation.version
            confirmation.status = "approved"
            confirmation.decided_at = decision_time
            confirmation.decided_by = confirmation_method
            confirmation.updated_at = decision_time
            confirmation.version += 1
            session.add(ManualConfirmationDecision(
                request_id=confirmation.id, decision="approve", request_version=prior_version,
                decided_by=confirmation_method,
                details_json={"kind": "player_profile", "subject_id": subject_id,
                              "candidate_id": source_player_id, "match": confirmation_method},
                created_at=decision_time,
            ))
            for sibling in confirmations:
                if sibling.id == confirmation.id or sibling.status != "pending":
                    continue
                sibling_version = sibling.version
                sibling.status = "superseded"
                sibling.decided_at = decision_time
                sibling.decided_by = confirmation_method
                sibling.updated_at = decision_time
                sibling.version += 1
                session.add(ManualConfirmationDecision(
                    request_id=sibling.id, decision="superseded", request_version=sibling_version,
                    decided_by=confirmation_method,
                    details_json={"superseded_by_request_id": str(confirmation.id)},
                    created_at=decision_time,
                ))
            enqueue_player_refresh(session, participant_id, "automatic_exact_identity_match")
            auto_confirmed_exact += 1
        # Upgrade legacy failures from the pre-backoff worker only for players
        # attached to matches currently inside the live/+6h research window.
        eligible_events = session.query(Event).filter(
            Event.sport_code == "tennis", Event.finished_at.is_(None),
            ~Event.participant_a_name.contains("/"), ~Event.participant_b_name.contains("/"),
            or_(
                (Event.is_live.is_(True) & (Event.last_seen_at >= now - timedelta(seconds=90))),
                (Event.is_live.is_(False) & (Event.scheduled_start_at >= now)
                 & (Event.scheduled_start_at <= now + timedelta(hours=6))),
            ),
        ).all()
        eligible_participant_ids = {pid for event in eligible_events
                                    for pid in (event.participant_a_id, event.participant_b_id) if pid}
        live_participant_ids = {
            pid for event in eligible_events
            if event.is_live and event.last_seen_at and event.last_seen_at >= now - timedelta(seconds=90)
            for pid in (event.participant_a_id, event.participant_b_id) if pid
        }
        if eligible_participant_ids:
            legacy_jobs = session.query(TennisPlayerRefreshJob).filter(
                TennisPlayerRefreshJob.participant_id.in_(eligible_participant_ids),
                TennisPlayerRefreshJob.status == "failed",
            ).with_for_update(skip_locked=True).all()
            for job in legacy_jobs:
                profile = session.get(TennisPlayerProfile, job.participant_id)
                if profile and profile.sync_status in {"ambiguous", "identity_confirmation_required"}:
                    job.status = "waiting_confirmation"
                    job.progress_json = {"stage": "identity_confirmation_required"}
                    job.next_run_at = now
                else:
                    no_profile = (job.last_error or "").casefold().find("not_found") >= 0 or bool(
                        profile and profile.sync_status == "not_found"
                    )
                    blocked_error = any(marker in (job.last_error or "").upper() for marker in ("403", "429", "BLOCKED"))
                    delay = timedelta(hours=6) if no_profile else (timedelta(minutes=15) if blocked_error else timedelta(minutes=5))
                    attempted_at = profile.last_attempt_at if profile else job.requested_at
                    job.next_run_at = max(now, attempted_at + delay) if attempted_at else now
                    job.status = "retry"
                    job.progress_json = {"stage": "legacy_failure_retry", "reason": job.last_error}
                job.attempt_token = None; job.lease_until = None; job.updated_at = now
        old_doubles_jobs = session.execute(
            select(TennisPlayerRefreshJob)
            .join(Participant, Participant.id == TennisPlayerRefreshJob.participant_id)
            .where(
                Participant.sport_code == "tennis",
                Participant.canonical_name.like("%/%"),
                TennisPlayerRefreshJob.status.in_(("queued", "running")),
            )
            .with_for_update(skip_locked=True)
        ).scalars().all()
        for job in old_doubles_jobs:
            job.status = "skipped"
            job.last_error = "DOUBLES_NOT_SUPPORTED"
            job.attempt_token = None
            job.lease_until = None
            job.updated_at = now
            skipped_doubles += 1
        # A proposal awaiting a human decision is parked durably and must not
        # be reclaimed even if another scheduler path queued the participant.
        pending_player_requests = session.query(ManualConfirmationRequest).filter_by(
            kind="player_profile", source="flashscore", status="pending"
        ).all()
        pending_player_ids = set()
        for confirmation in pending_player_requests:
            try:
                pending_player_ids.add(uuid.UUID(confirmation.subject_id))
            except ValueError:
                continue
        if pending_player_ids:
            parked_jobs = session.query(TennisPlayerRefreshJob).filter(
                TennisPlayerRefreshJob.participant_id.in_(pending_player_ids),
                TennisPlayerRefreshJob.status.in_(("queued", "retry", "running")),
            ).with_for_update(skip_locked=True).all()
            for job in parked_jobs:
                job.status = "waiting_confirmation"
                job.last_error = "IDENTITY_CONFIRMATION_REQUIRED"
                job.progress_json = {"stage": "identity_confirmation_required"}
                job.attempt_token = None
                job.lease_until = None
                job.completed_at = now
                job.updated_at = now
        # Older versions parked player work after an ambiguous search but did
        # not consistently keep the durable request row. A decision closes the
        # request permanently, so a parked job with no pending request must be
        # allowed to continue. The refresh path still verifies the current
        # confirmed identity and will open a new request if the new search is
        # ambiguous; this never auto-approves a candidate.
        waiting_jobs = session.query(TennisPlayerRefreshJob).filter(
            TennisPlayerRefreshJob.status == "waiting_confirmation",
        ).with_for_update(skip_locked=True).all()
        from .confirmation_recovery import resume_player_job_after_closed_confirmation
        resumed_orphaned_confirmations = 0
        for job in waiting_jobs:
            if job.participant_id in pending_player_ids:
                continue
            profile = session.get(TennisPlayerProfile, job.participant_id, with_for_update=True)
            resumed_orphaned_confirmations += int(
                resume_player_job_after_closed_confirmation(job, profile, now)
            )
        # If every profile slot is occupied by an old event, revoke those
        # leases before dispatching current-event work. The isolated scraper
        # checks its fencing token while navigating and terminates its child;
        # its eventual result write is rejected by the same token check.
        preempted_stale_jobs = 0
        if eligible_participant_ids:
            current_due = session.query(func.count(TennisPlayerRefreshJob.participant_id)).filter(
                TennisPlayerRefreshJob.participant_id.in_(eligible_participant_ids),
                or_(
                    ((TennisPlayerRefreshJob.status == "queued") |
                     (TennisPlayerRefreshJob.status == "retry"))
                    & (TennisPlayerRefreshJob.next_run_at <= now),
                    (TennisPlayerRefreshJob.status == "running")
                    & (TennisPlayerRefreshJob.lease_until <= now),
                ),
                ~TennisPlayerRefreshJob.participant_id.in_(pending_player_ids or {uuid.UUID(int=0)}),
            ).scalar() or 0
            if current_due:
                stale_active = session.query(TennisPlayerRefreshJob).filter(
                    _tennis_stale_running_job_filter(
                        TennisPlayerRefreshJob, eligible_participant_ids, now
                    )
                ).with_for_update(skip_locked=True).all()
                for job in stale_active:
                    job.status = "retry"
                    job.last_error = "PREEMPTED_FOR_CURRENT_EVENT"
                    job.next_run_at = now
                    job.attempt_token = None
                    job.lease_until = None
                    job.progress_json = {**(job.progress_json or {}), "stage": "preempted_for_current_event"}
                    job.updated_at = now
                    profile = session.get(TennisPlayerProfile, job.participant_id, with_for_update=True)
                    if profile and profile.sync_status == "running":
                        profile.sync_status = _profile_status_after_preemption(profile)
                        profile.sync_error = "REFRESH_PREEMPTED_FOR_CURRENT_EVENT"
                    preempted_stale_jobs += 1
        if preempted_stale_jobs:
            session.commit()
            return {"status": "dispatched", "claimed_count": 0, "active_count": 0,
                    "preempted_stale_jobs": preempted_stale_jobs,
                    "skipped_doubles": skipped_doubles,
                    "auto_confirmed_exact": auto_confirmed_exact,
                    "resumed_orphaned_confirmations": resumed_orphaned_confirmations,
                    "concurrency_limit": config.flashscore_tennis_profile_concurrency}
        active = session.query(func.count(TennisPlayerRefreshJob.participant_id)).filter(
            TennisPlayerRefreshJob.status == "running",
            TennisPlayerRefreshJob.lease_until > now,
        ).scalar() or 0
        slots = max(0, min(limit, config.flashscore_tennis_profile_concurrency - active))
        jobs = session.execute(
            select(TennisPlayerRefreshJob)
            .where(
                or_(
                    TennisPlayerRefreshJob.status == "queued",
                    (TennisPlayerRefreshJob.status == "retry") & (TennisPlayerRefreshJob.next_run_at <= now),
                    (TennisPlayerRefreshJob.status == "running") & (TennisPlayerRefreshJob.lease_until <= now),
                )
                , TennisPlayerRefreshJob.next_run_at <= now
            )
            .order_by(
                _tennis_player_dispatch_priority(
                    TennisPlayerRefreshJob.participant_id,
                    live_participant_ids,
                    eligible_participant_ids,
                ),
                TennisPlayerRefreshJob.next_run_at,
                TennisPlayerRefreshJob.requested_at,
            )
            .limit(slots)
            .with_for_update(skip_locked=True)
        ).scalars().all() if slots else []
        for job in jobs:
            participant = session.get(Participant, job.participant_id)
            if participant is None:
                job.status = "failed"
                job.last_error = "PARTICIPANT_NOT_FOUND"
                job.lease_until = None
                continue
            if is_doubles_player_display(participant.canonical_name):
                job.status = "skipped"
                job.last_error = "DOUBLES_NOT_SUPPORTED"
                job.attempt_token = None
                job.lease_until = None
                job.updated_at = now
                skipped_doubles += 1
                continue
            job.status = "running"
            job.attempts += 1
            job.lease_until = now + timedelta(minutes=5)
            job.attempt_token = str(uuid.uuid4())
            job.progress_json = {"stage": "claimed", "attempt": job.attempts}
            job.updated_at = now
            claimed.append((participant.id, participant.canonical_name, job.attempt_token))
        session.commit()
    for participant_id, participant_name, attempt_token in claimed:
        asyncio.create_task(asyncio.to_thread(_prepare_one_player, participant_id, participant_name, attempt_token))
    return {"status": "dispatched", "claimed_count": len(claimed),
            "active_count": active + len(claimed), "skipped_doubles": skipped_doubles,
            "auto_confirmed_exact": auto_confirmed_exact,
            "preempted_stale_jobs": 0,
            "resumed_orphaned_confirmations": resumed_orphaned_confirmations,
            "concurrency_limit": config.flashscore_tennis_profile_concurrency}


def _run_point_heartbeat(event_id: str, token: str, stop: threading.Event) -> None:
    from datetime import timedelta
    from db.connection import SessionLocal
    from db.models import TennisPointRefreshJob
    while not stop.wait(30):
        with SessionLocal() as session:
            job = session.query(TennisPointRefreshJob).filter_by(event_id=uuid.UUID(event_id), attempt_token=token, status="running").filter(
                TennisPointRefreshJob.lease_until > datetime.now(timezone.utc)
            ).with_for_update().first()
            if job is None: return
            job.lease_until = datetime.now(timezone.utc) + timedelta(minutes=3)
            job.updated_at = datetime.now(timezone.utc)
            session.commit()


def _record_point_job_event(session, job, available_at: datetime, source_match_id: str | None = None) -> None:
    from db.models import TennisPointRefreshJobEvent
    session.add(TennisPointRefreshJobEvent(
        event_id=job.event_id,
        status=job.status,
        last_error=job.last_error,
        source_match_id=source_match_id,
        available_at=available_at,
    ))


def _run_point_refresh(event_id: str, token: str) -> None:
    from datetime import timedelta
    from db.connection import SessionLocal
    from db.models import (Event, ManualConfirmationRequest, TennisMatchLookupJob,
                           TennisPointMatchLink, TennisPointRefreshJob)
    from .point_history import collect_event_point_history
    stop = threading.Event()
    heartbeat = threading.Thread(target=_run_point_heartbeat, args=(event_id, token, stop), daemon=True)
    heartbeat.start()
    try:
        with SessionLocal() as session:
            source_event = session.get(Event, uuid.UUID(event_id))
            source_priority = "live" if source_event and source_event.is_live else "archive"
        result = collect_event_point_history(
            event_id, token, source_access=partial(_flashscore_source_access, source_priority)
        )
        error = None if result.get("status") == "available" else str(
            result.get("reason") or result.get("status") or "unavailable"
        )
    except Exception as exc:
        logger.exception("Tennis point history refresh failed event=%s", event_id)
        result, error = {}, type(exc).__name__
    finally:
        stop.set(); heartbeat.join(timeout=1)
    with SessionLocal() as session:
        event = session.get(Event, uuid.UUID(event_id))
        job = session.query(TennisPointRefreshJob).filter_by(event_id=uuid.UUID(event_id), attempt_token=token, status="running").filter(
            TennisPointRefreshJob.lease_until > datetime.now(timezone.utc)
        ).with_for_update().first()
        if job is None:
            return
        finalized = bool(event and event.finished_at is not None and error is None)
        blocked = bool(error and ("BLOCKED" in error.upper() or "429" in error or "403" in error))
        waiting_confirmation = error in {"ambiguous", "waiting_confirmation"}
        if blocked:
            _record_flashscore_throttle()
        job.status = ("waiting_confirmation" if waiting_confirmation else
                      "finalized" if finalized else
                      "completed" if error is None else ("blocked" if blocked else "retry"))
        job.last_error = ("MATCH_CONFIRMATION_REQUIRED" if waiting_confirmation else
                          "FINALIZED" if finalized else (error[:128] if error else None))
        job.attempt_token = None
        job.lease_until = None
        from .config import config
        backoff = 900 if blocked else min(900, config.flashscore_tennis_point_poll_seconds * (2 ** min(job.attempts, 5)))
        job.next_run_at = (datetime.now(timezone.utc) if waiting_confirmation else
                           datetime.now(timezone.utc) + timedelta(seconds=config.flashscore_tennis_point_poll_seconds if error is None else backoff))
        job.updated_at = datetime.now(timezone.utc)
        _record_point_job_event(session, job, job.updated_at, result.get("source_match_id"))
        session.commit()


def _confirmed_flashscore_player_id(session, participant_id):
    if participant_id is None:
        return None
    from db.models import TennisPlayerIdentityLink
    rows = session.query(TennisPlayerIdentityLink).filter_by(
        participant_id=participant_id, source="flashscore"
    ).order_by(TennisPlayerIdentityLink.available_at.desc(), TennisPlayerIdentityLink.id.desc()).limit(20).all()
    latest = {}
    for row in rows:
        latest.setdefault(row.source_player_id, row)
    confirmed = [row.source_player_id for row in latest.values() if row.status == "confirmed"]
    return confirmed[0] if len(confirmed) == 1 else None


def auto_resolve_exact_match_confirmations(limit: int = 100) -> dict[str, int]:
    """Close stale match requests only when one candidate exactly names both known players.

    Multiple pending candidates, unconfirmed identities, reused match IDs, and
    URLs that do not exactly contain both confirmed player slugs stay manual.
    """
    from sqlalchemy import func, text
    from db.connection import SessionLocal
    from db.models import (
        Event, ManualConfirmationDecision, ManualConfirmationRequest,
        TennisMatchLookupJob, TennisPointMatchLink, TennisPointRefreshJob,
    )
    from .confirmation_recovery import exact_confirmed_match_orientation
    from .domain_filter import DomainFilter

    now = datetime.now(timezone.utc)
    resolved = skipped = 0
    with SessionLocal() as session:
        pending = session.query(ManualConfirmationRequest).filter_by(
            kind="event_match", source="flashscore", status="pending"
        ).order_by(ManualConfirmationRequest.created_at, ManualConfirmationRequest.id).limit(
            max(1, min(int(limit), 500))
        ).all()
        for snapshot in pending:
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {
                "key": f"confirmation-subject:{snapshot.source}:{snapshot.kind}:{snapshot.subject_id}"
            })
            item = session.query(ManualConfirmationRequest).filter_by(
                id=snapshot.id, status="pending"
            ).with_for_update().first()
            if item is None:
                continue
            candidate_count = session.query(func.count(ManualConfirmationRequest.id)).filter_by(
                kind=item.kind, source=item.source, subject_id=item.subject_id, status="pending"
            ).scalar() or 0
            try:
                event_id = uuid.UUID(item.subject_id)
            except ValueError:
                skipped += 1
                continue
            event = session.get(Event, event_id, with_for_update=True)
            if event is None or event.sport_code != "tennis":
                skipped += 1
                continue
            player_a_id = _confirmed_flashscore_player_id(session, event.participant_a_id)
            player_b_id = _confirmed_flashscore_player_id(session, event.participant_b_id)
            evidence = dict(item.evidence_json or {})
            candidate = evidence.get("candidate") or {}
            source_url = str(candidate.get("url") or evidence.get("source_url") or "")
            allowed, _reason = DomainFilter.is_allowed(source_url) if source_url else (False, "missing_url")
            orientation = exact_confirmed_match_orientation(
                source_url, item.candidate_id, player_a_id, player_b_id, int(candidate_count)
            ) if allowed else None
            if orientation is None:
                skipped += 1
                continue

            source_links = session.query(TennisPointMatchLink).filter_by(
                source_match_id=item.candidate_id
            ).filter(TennisPointMatchLink.available_at <= now).order_by(
                TennisPointMatchLink.available_at.desc(), TennisPointMatchLink.id.desc()
            ).all()
            latest_link = source_links[0] if source_links else None
            if latest_link and latest_link.status == "rejected":
                skipped += 1
                continue
            if latest_link and latest_link.status in {"matched", "manual"} and latest_link.fonbet_event_id not in (None, event_id):
                skipped += 1
                continue

            if not (latest_link and latest_link.status in {"matched", "manual"} and latest_link.fonbet_event_id == event_id):
                session.add(TennisPointMatchLink(
                    source_match_id=item.candidate_id, fonbet_event_id=event_id,
                    status="matched", confidence=1.0,
                    evidence_json={"method": "automatic_exact_confirmed_player_ids",
                        "request_id": str(item.id), "flashscore_player_ids": [player_a_id, player_b_id],
                        "source_url": source_url, "external_home_is_fonbet_a": orientation},
                    available_at=now,
                ))
            lookup = session.get(TennisMatchLookupJob, event_id, with_for_update=True)
            if lookup is None:
                lookup = TennisMatchLookupJob(event_id=event_id, status="matched", next_run_at=now)
                session.add(lookup)
            lookup.status = "matched"; lookup.source_match_id = item.candidate_id
            lookup.last_error = None; lookup.next_run_at = now
            lookup.attempt_token = None; lookup.lease_until = None; lookup.updated_at = now
            point_job = session.get(TennisPointRefreshJob, event_id, with_for_update=True)
            if point_job is None:
                point_job = TennisPointRefreshJob(event_id=event_id, status="queued",
                    next_run_at=now, last_requested_at=now, updated_at=now)
                session.add(point_job)
            else:
                point_job.status = "queued"; point_job.last_error = None
                point_job.next_run_at = now; point_job.last_requested_at = now
                point_job.attempt_token = None; point_job.lease_until = None; point_job.updated_at = now
            _record_point_job_event(session, point_job, now, item.candidate_id)

            prior_version = item.version
            item.status = "approved"; item.version += 1
            item.decided_at = now; item.decided_by = "system:auto_exact_confirmed_player_ids"
            item.updated_at = now
            session.add(ManualConfirmationDecision(
                request_id=item.id, decision="approve", request_version=prior_version,
                decided_by=item.decided_by, created_at=now,
                details_json={"kind": item.kind, "subject_id": item.subject_id,
                    "candidate_id": item.candidate_id, "method": "automatic_exact_confirmed_player_ids",
                    "player_ids": [player_a_id, player_b_id], "external_home_is_fonbet_a": orientation},
            ))
            resolved += 1
        session.commit()
    return {"resolved": resolved, "left_for_review": skipped}


def _run_match_lookup(event_id: str, token: str) -> None:
    from datetime import timedelta
    from db.connection import SessionLocal
    from db.models import (Event, ManualConfirmationRequest, TennisMatchLookupJob,
                           TennisPointMatchLink, TennisPointRefreshJob)
    from .point_history import _discover_live_match
    now = datetime.now(timezone.utc)
    event_uuid = uuid.UUID(event_id)
    with SessionLocal() as session:
        job = session.query(TennisMatchLookupJob).filter_by(
            event_id=event_uuid, attempt_token=token, status="running"
        ).filter(TennisMatchLookupJob.lease_until > now).with_for_update().first()
        event = session.get(Event, event_uuid)
        if job is None or event is None:
            return
        pending_confirmation = session.query(ManualConfirmationRequest.id).filter_by(
            kind="event_match", source="flashscore", subject_id=str(event_uuid), status="pending"
        ).first()
        if pending_confirmation:
            job.status = "waiting_confirmation"; job.last_error = "MATCH_CONFIRMATION_REQUIRED"
            job.attempt_token = None; job.lease_until = None; job.updated_at = now
            session.commit()
            return
        if (not event.is_live or event.finished_at is not None or not event.last_seen_at
                or event.last_seen_at < now - timedelta(seconds=90)):
            job.status = "waiting_match"; job.last_error = "EVENT_NO_LONGER_FRESH_LIVE"
            job.next_run_at = now + timedelta(minutes=10); job.attempt_token = None; job.lease_until = None
            job.updated_at = now; session.commit(); return
        player_a_id = _confirmed_flashscore_player_id(session, event.participant_a_id)
        player_b_id = _confirmed_flashscore_player_id(session, event.participant_b_id)
        if not player_a_id or not player_b_id:
            job.status = "waiting_players"; job.last_error = "PLAYER_SOURCE_IDS_MISSING"
            job.next_run_at = now + timedelta(minutes=10); job.attempt_token = None; job.lease_until = None
            job.updated_at = now; session.commit(); return
        event_names = [event.participant_a_name, event.participant_b_name]
        rejected_match_ids = {
            link.source_match_id for link in session.query(TennisPointMatchLink).filter_by(
                fonbet_event_id=event_uuid, status="rejected"
            ).all()
        }
        require_manual_confirmation = job.last_error == "MANUAL_CANDIDATE_REJECTED"
    try:
        match = _discover_live_match(player_a_id, player_b_id,
            source_access=partial(_flashscore_source_access, "live"),
            excluded_match_ids=rejected_match_ids)
        error = None
    except Exception as exc:
        logger.info("Flashscore match lookup failed event=%s error=%s", event_id, type(exc).__name__)
        match, error = None, type(exc).__name__
    with SessionLocal() as session:
        job = session.query(TennisMatchLookupJob).filter_by(
            event_id=event_uuid, attempt_token=token, status="running"
        ).filter(TennisMatchLookupJob.lease_until > datetime.now(timezone.utc)).with_for_update().first()
        if job is None:
            return
        now = datetime.now(timezone.utc)
        from db.tennis_confirmations import upsert_manual_confirmation
        if match and match.get("status") == "matched" and require_manual_confirmation:
            match = {"status": "ambiguous", "candidates": [{
                "source_match_id": match["source_match_id"], "url": match.get("url")
            }]}
        if match and match.get("status") == "matched":
            source_match_id = match["source_match_id"]
            session.add(TennisPointMatchLink(source_match_id=source_match_id, fonbet_event_id=event_uuid,
                status="matched", confidence=1.0, evidence_json={
                    "method": "confirmed_flashscore_player_ids_on_live_page",
                    "flashscore_player_ids": [player_a_id, player_b_id],
                    "fonbet_participants": event_names, "source_url": match.get("url"),
                }, available_at=now))
            job.status = "matched"; job.source_match_id = source_match_id; job.last_error = None
            point_job = session.get(TennisPointRefreshJob, event_uuid, with_for_update=True)
            if point_job:
                point_job.status = "queued"; point_job.last_error = None; point_job.next_run_at = now
                point_job.updated_at = now; point_job.last_requested_at = now
        elif match and match.get("status") == "ambiguous":
            job.status = "waiting_confirmation"; job.last_error = "MULTIPLE_LIVE_MATCH_CANDIDATES"
            for candidate in match.get("candidates", []):
                if not candidate.get("source_match_id"):
                    continue
                session.add(TennisPointMatchLink(source_match_id=candidate["source_match_id"], fonbet_event_id=None,
                    status="ambiguous", confidence=0.5, evidence_json={
                        "candidate_event_id": event_id, "source_url": candidate.get("url"),
                        "flashscore_player_ids": [player_a_id, player_b_id],
                    }, available_at=now))
                upsert_manual_confirmation(session, kind="event_match", subject_id=event_id,
                    candidate_id=str(candidate["source_match_id"]), evidence={
                        "event_id": event_id, "participant_a_name": event_names[0],
                        "participant_b_name": event_names[1], "fonbet_participants": event_names,
                        "source_match_id": str(candidate["source_match_id"]),
                        "source_url": candidate.get("url"), "candidate": candidate,
                        "flashscore_player_ids": [player_a_id, player_b_id],
                    })
        else:
            job.status = "retry"
            job.last_error = error or ("NO_ALTERNATIVE_MATCH_DATA" if require_manual_confirmation else "LIVE_MATCH_NOT_FOUND")
            job.next_run_at = now + (timedelta(minutes=15) if error and ("403" in error or "429" in error or "BLOCKED" in error)
                                     else timedelta(hours=6) if require_manual_confirmation and not error
                                     else timedelta(seconds=60))
        job.attempt_token = None; job.lease_until = None; job.updated_at = now
        session.commit()


@app.post("/api/tennis-history/match-search/dispatch")
async def dispatch_tennis_match_search(limit: int = 8) -> Dict[str, Any]:
    """Create, coalesce, and dispatch durable lookups for live tennis matches."""
    from datetime import timedelta
    from sqlalchemy import func, or_, select, text
    from db.connection import SessionLocal
    from db.models import Event, ManualConfirmationRequest, TennisMatchLookupJob, TennisPointMatchLink
    now = datetime.now(timezone.utc)
    claimed = []
    with SessionLocal() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('neurobet:tennis-match-lookup-dispatch'))"))
        events = session.query(Event).filter(Event.sport_code == "tennis", Event.is_live.is_(True),
            Event.finished_at.is_(None), Event.last_seen_at >= now - timedelta(seconds=90),
            ~Event.participant_a_name.contains("/"), ~Event.participant_b_name.contains("/")).all()
        for event in events:
            player_a_id = _confirmed_flashscore_player_id(session, event.participant_a_id)
            player_b_id = _confirmed_flashscore_player_id(session, event.participant_b_id)
            source_links = session.query(TennisPointMatchLink).filter_by(fonbet_event_id=event.id).order_by(
                TennisPointMatchLink.available_at.desc(), TennisPointMatchLink.id.desc()).limit(20).all()
            latest_sources = {}
            for link in source_links:
                latest_sources.setdefault(link.source_match_id, link)
            has_source = any(link.status in {"matched", "manual"} for link in latest_sources.values())
            job = session.get(TennisMatchLookupJob, event.id, with_for_update=True)
            pending_confirmation = session.query(ManualConfirmationRequest.id).filter_by(
                kind="event_match", source="flashscore", subject_id=str(event.id), status="pending"
            ).first() is not None
            if pending_confirmation:
                if job is None:
                    session.add(TennisMatchLookupJob(event_id=event.id, status="waiting_confirmation",
                        last_error="MATCH_CONFIRMATION_REQUIRED", next_run_at=now))
                elif job.status != "matched":
                    job.status = "waiting_confirmation"; job.last_error = "MATCH_CONFIRMATION_REQUIRED"
                    job.attempt_token = None; job.lease_until = None; job.next_run_at = now; job.updated_at = now
                continue
            if job is None:
                initial_status = "queued" if player_a_id and player_b_id and not has_source else ("matched" if has_source else "waiting_players")
                job = TennisMatchLookupJob(event_id=event.id, status=initial_status, next_run_at=now)
                session.add(job); session.flush()
            elif has_source:
                job.status = "matched"; job.last_error = None
            elif job.status == "waiting_players" and player_a_id and player_b_id:
                job.status = "queued"; job.next_run_at = now; job.updated_at = now
            elif job.status == "retry" and job.next_run_at <= now:
                job.status = "queued"; job.next_run_at = now; job.updated_at = now
            elif job.status == "waiting_confirmation":
                # The request may have been approved/rejected while this lookup
                # was parked. With no pending request, resume only the lookup;
                # ambiguity will create a fresh immutable request as usual.
                point_job = session.get(TennisPointRefreshJob, event.id, with_for_update=True)
                from .confirmation_recovery import resume_match_job_after_closed_confirmation
                resume_match_job_after_closed_confirmation(
                    job, point_job, bool(player_a_id and player_b_id), now
                )
        active = session.query(func.count(TennisMatchLookupJob.event_id)).filter(
            TennisMatchLookupJob.status == "running", TennisMatchLookupJob.lease_until > now).scalar() or 0
        slots = max(0, min(max(1, min(limit, 20)), 2 - active))
        jobs = session.execute(select(TennisMatchLookupJob).where(
            or_(TennisMatchLookupJob.status == "queued",
                (TennisMatchLookupJob.status == "running") & (TennisMatchLookupJob.lease_until <= now)),
            TennisMatchLookupJob.next_run_at <= now).order_by(TennisMatchLookupJob.next_run_at)
            .limit(slots).with_for_update(skip_locked=True)).scalars().all() if slots else []
        for job in jobs:
            job.status = "running"; job.attempts += 1; job.attempt_token = str(uuid.uuid4())
            job.lease_until = now + timedelta(minutes=3); job.updated_at = now
            claimed.append((str(job.event_id), job.attempt_token))
        session.commit()
    for event_id, token in claimed:
        asyncio.create_task(asyncio.to_thread(_run_match_lookup, event_id, token))
    return {"status": "dispatched", "claimed_count": len(claimed), "active_count": active + len(claimed)}


@app.post("/api/tennis-history/archive-backfill/dispatch")
async def dispatch_tennis_archive_backfill(limit: int = 4) -> Dict[str, Any]:
    """Enrol completed Fonbet singles whose Flashscore result history is known.

    This is deliberately a database-only scheduling step. The existing durable
    point-refresh queue performs browser work, with archive priority and the
    same lease fencing/rate limiter as live PBP collection.
    """
    from datetime import timedelta
    import re
    from sqlalchemy import or_
    from db.connection import SessionLocal
    from db.models import (
        Event, ExtTennisMatch, League, ManualConfirmationRequest,
        TennisPlayerProfile, TennisPlayerRefreshJob, TennisPointDocument,
        TennisPointMatchLink, TennisPointRefreshJob,
    )
    from .player_profiles import enqueue_player_refresh, _post_match_profile_refresh_due
    from .tennis_flashscore import _player_name_matches
    from db.tennis_confirmations import upsert_manual_confirmation

    now = datetime.now(timezone.utc)
    limit = max(1, min(limit, 20))
    counts = {"scanned": 0, "queued": 0, "waiting_players": 0,
              "waiting_match": 0, "waiting_confirmation": 0, "already_complete": 0}
    with SessionLocal() as session:
        # The full completed archive is eligible for training-data collection;
        # the +6h forecasting window does not apply to this historical queue.
        events = session.query(Event).filter(
            Event.sport_code == "tennis", Event.source == "fonbet",
            Event.status == "finished", Event.finished_at.is_not(None),
            Event.source_event_id.op("~")(r"^[0-9]+$"),
            ~Event.participant_a_name.contains("/"),
            ~Event.participant_b_name.contains("/"),
        ).order_by(Event.finished_at.asc(), Event.id.asc()).all()
        counts["scanned"] = len(events)
        event_ids = [event.id for event in events]
        event_subject_ids = [str(event_id) for event_id in event_ids]
        pending_confirmation_event_ids = {
            row[0] for row in session.query(ManualConfirmationRequest.subject_id).filter(
                ManualConfirmationRequest.kind == "event_match",
                ManualConfirmationRequest.source == "flashscore",
                ManualConfirmationRequest.status == "pending",
                ManualConfirmationRequest.subject_id.in_(event_subject_ids),
            ).all()
        } if event_subject_ids else set()
        league_ids = {event.league_id for event in events if event.league_id}
        league_names = dict(session.query(League.id, League.name).filter(
            League.id.in_(league_ids)
        ).all()) if league_ids else {}
        participant_ids = {pid for event in events
                           for pid in (event.participant_a_id, event.participant_b_id) if pid}
        profiles = {row.participant_id: row for row in session.query(TennisPlayerProfile).filter(
            TennisPlayerProfile.participant_id.in_(participant_ids)
        ).all()} if participant_ids else {}
        refresh_jobs = {row.participant_id: row for row in session.query(TennisPlayerRefreshJob).filter(
            TennisPlayerRefreshJob.participant_id.in_(participant_ids)
        ).all()} if participant_ids else {}
        source_ids = {str((profiles.get(pid).source_identities or {}).get("flashscore"))
                      for pid in participant_ids if profiles.get(pid)
                      and (profiles[pid].source_identities or {}).get("flashscore")}
        if source_ids:
            low = min(event.scheduled_start_at for event in events) - timedelta(days=2)
            high = max(event.scheduled_start_at for event in events) + timedelta(days=2)
            source_matches = session.query(ExtTennisMatch).filter(
                ExtTennisMatch.source == "flashscore",
                ExtTennisMatch.played_at.between(low, high),
                ExtTennisMatch.winner_player_id.in_(source_ids),
                ExtTennisMatch.loser_player_id.in_(source_ids),
            ).all()
        else:
            source_matches = []
        matches_by_players: dict[frozenset[str], list[Any]] = {}
        for match in source_matches:
            if match.winner_player_id and match.loser_player_id:
                matches_by_players.setdefault(
                    frozenset((str(match.winner_player_id), str(match.loser_player_id))), []
                ).append(match)

        links = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.fonbet_event_id.in_(event_ids),
            TennisPointMatchLink.available_at <= now,
        ).order_by(TennisPointMatchLink.available_at.desc(), TennisPointMatchLink.id.desc()).all() if events else []
        links_by_event: dict[Any, dict[str, Any]] = {}
        for link in links:
            event_links = links_by_event.setdefault(link.fonbet_event_id, {})
            event_links.setdefault(link.source_match_id, link)
        jobs = {row.event_id: row for row in session.query(TennisPointRefreshJob).filter(
            TennisPointRefreshJob.event_id.in_(event_ids)
        ).with_for_update().all()} if events else {}
        docs = session.query(TennisPointDocument.source_match_id).filter(
            TennisPointDocument.source_match_id.in_({link.source_match_id for link in links}),
            TennisPointDocument.available_at.is_not(None),
        ).distinct().all() if links else []
        documented_sources = {row[0] for row in docs}
        profiles_to_refresh = 0

        # Refresh players from newly completed matches before spending the
        # bounded profile-import budget on the older historical backfill. The
        # profile snapshots were often fetched while the match was still live,
        # so treating them as fresh for 30 days would miss the just-finished
        # result and leave its PBP lookup waiting until the next monthly scan.
        recent_cutoff = now - timedelta(days=1)
        # Only events first seen by Fonbet in the recent window belong to the
        # post-match refresh lane. Archive imports may have a recently touched
        # finished_at field despite being years old, so that field alone would
        # let the historical backlog starve genuinely new match completions.
        recent_refresh_budget = max(128, limit * 64)
        # Prioritize archives that have already spent time waiting for a
        # Flashscore match lookup. A large batch of newly completed events
        # can otherwise consume this bounded refresh budget on every pass.
        recent_events = sorted(
            events,
            key=lambda event: (
                -int(getattr(jobs.get(event.id), "attempts", 0) or 0),
                event.finished_at or now,
                str(event.id),
            ),
        )
        for event in recent_events:
            if (event.finished_at is None or event.finished_at < recent_cutoff
                    or event.first_seen_at is None or event.first_seen_at < recent_cutoff):
                continue
            point_job = jobs.get(event.id)
            if (point_job is None or point_job.status != "waiting_players"
                    or point_job.last_error != "HISTORICAL_FLASHSCORE_MATCH_NOT_INDEXED"):
                continue
            for participant_id in (event.participant_a_id, event.participant_b_id):
                if not participant_id or profiles_to_refresh >= recent_refresh_budget:
                    continue
                profile = profiles.get(participant_id)
                refresh_job = refresh_jobs.get(participant_id)
                if _post_match_profile_refresh_due(profile, refresh_job, event.finished_at, now):
                    refresh_jobs[participant_id] = enqueue_player_refresh(
                        session, participant_id, "historical_pbp_backfill"
                    )
                    profiles_to_refresh += 1

        for event in events:
            if counts["queued"] >= limit:
                break
            official = (event.metadata_json or {}).get("fonbet_official_result", {})
            if (not isinstance(official, dict) or str(official.get("status") or "").upper() != "FINISHED"
                    or official.get("is_walkover") or official.get("is_retired")
                    or str(official.get("winner") or "").casefold() not in {"player_a", "player_b"}):
                continue
            job = jobs.get(event.id)
            if job and job.status == "finalized":
                counts["already_complete"] += 1
                continue
            current_links = links_by_event.get(event.id, {})
            pending_confirmation = str(event.id) in pending_confirmation_event_ids
            if pending_confirmation:
                if job is None:
                    job = TennisPointRefreshJob(event_id=event.id, status="waiting_confirmation",
                        next_run_at=now, last_error="MATCH_CONFIRMATION_REQUIRED", updated_at=now)
                    session.add(job); jobs[event.id] = job
                elif job.status != "waiting_confirmation" or job.last_error != "MATCH_CONFIRMATION_REQUIRED":
                    job.status = "waiting_confirmation"; job.last_error = "MATCH_CONFIRMATION_REQUIRED"
                    job.next_run_at = now; job.attempt_token = None; job.lease_until = None; job.updated_at = now
                counts["waiting_confirmation"] += 1
                continue
            confirmed_source_ids = [source_id for source_id, link in current_links.items()
                                    if link.status in {"matched", "manual"}]
            rejected_source_ids = {source_id for source_id, link in current_links.items()
                                   if link.status == "rejected"}
            if confirmed_source_ids and any(source_id in documented_sources for source_id in confirmed_source_ids):
                if job and job.status in {"completed", "finalized"}:
                    counts["already_complete"] += 1
                    continue

            profile_a, profile_b = profiles.get(event.participant_a_id), profiles.get(event.participant_b_id)
            flashscore_a = str((profile_a.source_identities or {}).get("flashscore") or "") if profile_a else ""
            flashscore_b = str((profile_b.source_identities or {}).get("flashscore") or "") if profile_b else ""
            candidates = []
            if flashscore_a and flashscore_b:
                candidates = [match for match in matches_by_players.get(frozenset((flashscore_a, flashscore_b)), [])
                              if abs((match.played_at - event.scheduled_start_at).total_seconds()) <= 2 * 86400]
                league_name = league_names.get(event.league_id)
                if league_name:
                    wanted = {token for token in re.findall(r"[a-z0-9]+", league_name.casefold()) if len(token) > 3}
                    if wanted:
                        candidates = [match for match in candidates if wanted.intersection(
                            re.findall(r"[a-z0-9]+", str(match.tournament or "").casefold()))]
                candidates = [match for match in candidates if match.source_match_id not in rejected_source_ids]
            if confirmed_source_ids:
                candidates = [match for match in source_matches if match.source_match_id in confirmed_source_ids]

            if len(candidates) > 1:
                from db.models import TennisPointMatchLink
                for candidate in candidates:
                    already = current_links.get(candidate.source_match_id)
                    if already and already.status in {"rejected", "manual", "matched"}:
                        continue
                    session.add(TennisPointMatchLink(
                        source_match_id=candidate.source_match_id, fonbet_event_id=event.id,
                        status="ambiguous", confidence=0.5,
                        evidence_json={"method": "historical_player_pair_date_tournament",
                            "candidate_event_id": str(event.id), "fonbet_players": [event.participant_a_name, event.participant_b_name],
                            "flashscore_players": [candidate.winner_name, candidate.loser_name],
                            "tournament": candidate.tournament, "played_at": candidate.played_at.isoformat(),
                            "source_url": (candidate.stats or {}).get("source_url")},
                        available_at=now,
                    ))
                    upsert_manual_confirmation(session, kind="event_match", subject_id=str(event.id),
                        candidate_id=str(candidate.source_match_id), evidence={
                            "event_id": str(event.id), "participant_a_name": event.participant_a_name,
                            "participant_b_name": event.participant_b_name,
                            "source_match_id": candidate.source_match_id,
                            "source_url": (candidate.stats or {}).get("source_url"),
                            "candidate": {"home_name": candidate.winner_name, "away_name": candidate.loser_name,
                                          "tournament": candidate.tournament, "played_at": candidate.played_at.isoformat()},
                        })
                if job is None:
                    job = TennisPointRefreshJob(event_id=event.id, status="waiting_confirmation", next_run_at=now,
                                                last_error="MATCH_CONFIRMATION_REQUIRED", updated_at=now)
                    session.add(job); jobs[event.id] = job
                else:
                    job.status = "waiting_confirmation"; job.last_error = "MATCH_CONFIRMATION_REQUIRED"
                    job.next_run_at = now; job.attempt_token = None; job.lease_until = None
                    job.updated_at = now
                counts["waiting_confirmation"] += 1
                continue

            if not candidates:
                for participant_id, profile in ((event.participant_a_id, profile_a), (event.participant_b_id, profile_b)):
                    if not participant_id:
                        continue
                    refresh_job = refresh_jobs.get(participant_id)
                    if (_post_match_profile_refresh_due(profile, refresh_job, event.finished_at, now)
                            and profiles_to_refresh < limit * 2):
                        refresh_jobs[participant_id] = enqueue_player_refresh(
                            session, participant_id, "historical_pbp_backfill"
                        )
                        profiles_to_refresh += 1
                if job is None:
                    session.add(TennisPointRefreshJob(event_id=event.id, status="waiting_players",
                        next_run_at=now + timedelta(hours=6), last_error="HISTORICAL_FLASHSCORE_MATCH_NOT_INDEXED",
                        updated_at=now))
                    jobs[event.id] = True
                elif job.status not in {"running", "finalized"}:
                    job.status = "waiting_players"; job.last_error = "HISTORICAL_FLASHSCORE_MATCH_NOT_INDEXED"
                    job.next_run_at = now + timedelta(hours=6); job.attempt_token = None; job.lease_until = None
                    job.updated_at = now
                counts["waiting_players"] += 1
                continue

            if job is None:
                job = TennisPointRefreshJob(event_id=event.id, status="queued", next_run_at=now,
                    last_error=None, updated_at=now)
                session.add(job); jobs[event.id] = job
                counts["queued"] += 1
            elif job.status in {"waiting_players", "waiting_match"} or (
                    job.status not in {"running", "finalized"} and job.next_run_at <= now):
                job.status = "queued"; job.last_error = None; job.next_run_at = now; job.updated_at = now
                counts["queued"] += 1
        session.commit()
    return {"status": "enrolled", **counts, "player_refreshes_enqueued": profiles_to_refresh,
            "limit": limit, "forecast_window_hours": 6}


@app.post("/api/tennis-history/point-history/dispatch")
async def dispatch_live_point_history(limit: int = 8) -> Dict[str, Any]:
    """Queue eligible live match PBP and dispatch due durable jobs."""
    from datetime import timedelta
    from sqlalchemy import func, or_, select, text
    from db.connection import SessionLocal
    from db.models import (Event, ManualConfirmationRequest, TennisMatchLookupJob,
                           TennisPointMatchLink, TennisPointRefreshJob)
    now = datetime.now(timezone.utc)
    limit = max(1, min(limit, 30))
    claimed = []
    with SessionLocal() as session:
        # Serialize dispatchers so the database-wide active count is a real cap.
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('neurobet:tennis-point-dispatch'))"))
        live_events = session.query(Event).filter(
            Event.sport_code == "tennis", Event.is_live.is_(True),
            Event.last_seen_at >= now - timedelta(seconds=90),
            Event.finished_at.is_(None),
            ~Event.participant_a_name.contains("/"),
            ~Event.participant_b_name.contains("/"),
        ).order_by(Event.last_seen_at.desc()).all()
        for event in live_events:
            player_a_id = _confirmed_flashscore_player_id(session, event.participant_a_id)
            player_b_id = _confirmed_flashscore_player_id(session, event.participant_b_id)
            links = session.query(TennisPointMatchLink).filter_by(fonbet_event_id=event.id).order_by(
                TennisPointMatchLink.available_at.desc(), TennisPointMatchLink.id.desc()).limit(20).all()
            latest_links = {}
            for link in links:
                latest_links.setdefault(link.source_match_id, link)
            has_source = any(link.status in {"matched", "manual"} for link in latest_links.values())
            job = session.get(TennisPointRefreshJob, event.id)
            pending_match_confirmation = session.query(ManualConfirmationRequest.id).filter_by(
                kind="event_match", source="flashscore", subject_id=str(event.id), status="pending"
            ).first() is not None
            if job is None:
                job = TennisPointRefreshJob(
                    event_id=event.id,
                    status="waiting_confirmation" if pending_match_confirmation else "queued",
                    next_run_at=now,
                    last_error="MATCH_CONFIRMATION_REQUIRED" if pending_match_confirmation else None,
                )
                session.add(job); session.flush()
                _record_point_job_event(session, job, now)
            if pending_match_confirmation:
                if job.status != "waiting_confirmation" or job.last_error != "MATCH_CONFIRMATION_REQUIRED":
                    job.status = "waiting_confirmation"; job.last_error = "MATCH_CONFIRMATION_REQUIRED"
                    job.attempt_token = None; job.lease_until = None; job.next_run_at = now
                    job.updated_at = now; _record_point_job_event(session, job, now)
                lookup = session.get(TennisMatchLookupJob, event.id, with_for_update=True)
                if lookup is not None and lookup.status != "matched":
                    lookup.status = "waiting_confirmation"; lookup.last_error = "MATCH_CONFIRMATION_REQUIRED"
                    lookup.attempt_token = None; lookup.lease_until = None; lookup.next_run_at = now
                    lookup.updated_at = now
                continue
            if not has_source:
                waiting_status = "waiting_match" if player_a_id and player_b_id else "waiting_players"
                waiting_reason = "FLASHSCORE_MATCH_LINK_PENDING" if player_a_id and player_b_id else "PLAYER_SOURCE_IDS_MISSING"
                if job.status != waiting_status or job.last_error != waiting_reason:
                    job.status = waiting_status; job.last_error = waiting_reason
                    job.attempt_token = None; job.lease_until = None; job.next_run_at = now + timedelta(minutes=10)
                    job.updated_at = now; _record_point_job_event(session, job, now)
                if player_a_id and player_b_id:
                    lookup = session.get(TennisMatchLookupJob, event.id, with_for_update=True)
                    if lookup is None:
                        session.add(TennisMatchLookupJob(event_id=event.id, status="queued", next_run_at=now))
                    elif lookup.status in {"waiting_players", "retry"}:
                        lookup.status = "queued"; lookup.next_run_at = now; lookup.updated_at = now
                continue
            elif job.status in {"waiting_players", "waiting_match"} or (job.status in {"completed", "retry", "blocked", "queued"} and job.next_run_at <= now):
                if job.status != "queued":
                    job.status = "queued"
                    job.last_requested_at = now
                    _record_point_job_event(session, job, now)
        finished = session.query(Event).filter(
            Event.sport_code == "tennis", Event.finished_at.isnot(None),
            Event.finished_at >= now - timedelta(days=30),
        ).order_by(Event.finished_at.desc()).all()
        for event in finished:
            job = session.get(TennisPointRefreshJob, event.id)
            if job and job.status in {"completed", "retry", "blocked"} and job.last_error != "FINALIZED" and job.next_run_at <= now:
                job.status = "queued"; job.next_run_at = now; job.last_requested_at = now
                _record_point_job_event(session, job, now)
        session.flush()
        active = session.query(func.count(TennisPointRefreshJob.event_id)).filter(
            TennisPointRefreshJob.status == "running",
            TennisPointRefreshJob.lease_until > now,
        ).scalar() or 0
        slots = max(0, min(limit, config.flashscore_tennis_point_concurrency - active))
        jobs = session.execute(select(TennisPointRefreshJob).where(
            or_(TennisPointRefreshJob.status == "queued",
                (TennisPointRefreshJob.status == "running") & (TennisPointRefreshJob.lease_until <= now)),
            TennisPointRefreshJob.next_run_at <= now).join(
                Event, Event.id == TennisPointRefreshJob.event_id
            ).order_by(Event.is_live.desc(), TennisPointRefreshJob.next_run_at,
                       TennisPointRefreshJob.last_requested_at).limit(slots)
            .with_for_update(skip_locked=True)).scalars().all() if slots else []
        for job in jobs:
            event = session.get(Event, job.event_id)
            if event is None or (not event.is_live and event.finished_at is None):
                job.status = "completed"; job.attempt_token = None; job.lease_until = None
                continue
            job.status = "running"; job.attempts += 1
            job.attempt_token = str(uuid.uuid4()); job.lease_until = now + timedelta(minutes=3)
            job.updated_at = now
            _record_point_job_event(session, job, now)
            claimed.append((str(job.event_id), job.attempt_token))
        session.commit()
    for event_id, token in claimed:
        asyncio.create_task(asyncio.to_thread(_run_point_refresh, event_id, token))
    return {"status": "dispatched", "claimed_count": len(claimed),
            "active_count": active + len(claimed), "concurrency_limit": config.flashscore_tennis_point_concurrency}



if __name__ == "__main__":
    import uvicorn
    update_healthy_file()
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
