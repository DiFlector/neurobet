"""Historical replay jobs published by the backend and executed inside neural."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional

logger = logging.getLogger("neural.backtest_jobs")

STREAM = "backtest.jobs"
GROUP = "neural-backtest"
CONSUMER = "neural-1"


def _client():
    import redis

    url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    return redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)


def _ensure_group(client) -> None:
    try:
        client.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def claim_backtest_job() -> Optional[Dict[str, Any]]:
    try:
        client = _client()
        _ensure_group(client)
        for stream_id in ("0", ">"):
            entries = client.xreadgroup(GROUP, CONSUMER, {STREAM: stream_id}, count=1, block=1)
            job = _first_job(entries)
            if job is None:
                continue
            if client.get(_done_key(job["message_id"])):
                client.xack(STREAM, GROUP, job["redis_id"])
                continue
            fails = int(client.get(_fail_key(job["redis_id"])) or 0)
            if fails > 2:
                logger.error("Dropping backtest job %s after %d failures", job["message_id"], fails)
                client.xack(STREAM, GROUP, job["redis_id"])
                continue
            return job
    except Exception as exc:
        logger.warning("Backtest queue is unavailable: %s", exc)
    return None


def finish_backtest_job(job: Dict[str, Any], ok: bool) -> None:
    try:
        client = _client()
        if ok:
            client.delete(_fail_key(job["redis_id"]))
            client.set(_done_key(job["message_id"]), "1", ex=60 * 60 * 24 * 30)
            client.xack(STREAM, GROUP, job["redis_id"])
            return
        client.incr(_fail_key(job["redis_id"]))
        logger.info("Backtest job %s stays pending for a retry", job["message_id"])
    except Exception as exc:
        logger.warning("Could not update backtest job %s: %s", job.get("message_id"), exc)


def run_backtest_job(job: Dict[str, Any]) -> None:
    from db.connection import SessionLocal
    from db.models.ml_registry import BacktestRun

    payload = job.get("payload") or {}
    run_id = uuid.UUID(str(payload["run_id"]))
    with SessionLocal() as session:
        run = session.get(BacktestRun, run_id)
        if run is None:
            logger.info("Backtest run %s was deleted before start", run_id)
            return
        if run.status == "completed":
            return
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        run.error = None
        session.commit()
        try:
            _execute(session, run)
            fresh = session.get(BacktestRun, run_id)
            if fresh is None:
                logger.info("Backtest run %s was deleted during replay", run_id)
                return
            fresh.status = "completed"
            fresh.finished_at = datetime.now(timezone.utc)
            session.commit()
        except Exception as exc:
            session.rollback()
            failed = session.get(BacktestRun, run_id)
            if failed is not None:
                failed.status = "failed"
                failed.error = str(exc)[:2000]
                failed.finished_at = datetime.now(timezone.utc)
                session.commit()
            raise


def _execute(session, run) -> list:
    from db.models.events import Event, EventStateSnapshot
    from db.models.ml_registry import ModelVersion
    from db.models.odds import OddsSnapshot
    from db.models.predictions import LLMDecision
    from features import registry as feature_registry
    from sqlalchemy import select

    from db.models.ml_registry import BacktestRunResult

    from ..composite_predictor import load_predictor
    from ..versioning import is_test_tag
    from .engine import ReplayRunner

    request = run.request_json or {}
    if request.get("kind") == "strategy_search":
        _execute_strategy_search(session, run, request)
        return []
    selector = request.get("selector") or {}
    config = request.get("config") or {}
    sport = str(selector.get("sport_code") or run.sport_code or "tennis").lower()
    query = session.query(ModelVersion).filter(
        ModelVersion.market == "match_winner",
        ModelVersion.status.in_(["ACTIVE", "SHADOW"]),
        ModelVersion.sport_code == sport,
    )
    if selector.get("scope") == "model":
        query = query.filter(ModelVersion.id == uuid.UUID(str(selector["model_version_id"])))
    rows = [row for row in query.order_by(ModelVersion.created_at.desc()).all() if not is_test_tag(row.version_tag)]
    if not rows:
        raise RuntimeError("No ACTIVE or SHADOW model matched the selector")
    builder = feature_registry.get(sport)
    if builder is None:
        raise RuntimeError(f"No feature builder for {sport}")
    requested_ids = [str(item) for item in (config.get("event_ids") or [])]
    if requested_ids:
        try:
            parsed_ids = [uuid.UUID(item) for item in requested_ids]
        except ValueError as exc:
            raise RuntimeError("event_ids must contain UUIDs") from exc
        found = list(
            session.execute(
                select(Event)
                .where(Event.id.in_(parsed_ids))
                .where(Event.sport_code == sport)
                .where(Event.status.in_(["finished", "completed"]))
            ).scalars()
        )
        by_id = {str(event.id): event for event in found}
        missing = [item for item in requested_ids if item not in by_id]
        if missing:
            raise RuntimeError(f"Replay events are missing or unfinished: {missing[:5]}")
        events = [by_id[item] for item in requested_ids]
    else:
        limit = int(config.get("match_limit") or 100)
        events = list(
            session.execute(
                select(Event)
                .where(Event.sport_code == sport)
                .where(Event.status.in_(["finished", "completed"]))
                .where(Event.source_event_id.op("~")(r"^[0-9]+$"))
                .order_by(Event.scheduled_start_at.desc().nullslast(), Event.id.desc())
                .limit(limit)
            ).scalars()
        )
    cached = _cached_by_event(sport, builder, [str(event.id) for event in events])
    missing = [event for event in events if cached is None or str(event.id) not in cached]
    history = None
    if missing and sport == "tennis":
        from ..players import PlayerHistory

        history = PlayerHistory.load(session)
    matches = []
    for event in events:
        stored = None if cached is None else cached.get(str(event.id))
        if stored is None:
            loaded = _load_match(session, event, history, EventStateSnapshot, OddsSnapshot, LLMDecision)
        else:
            loaded = _load_cached_match(session, event, stored, OddsSnapshot)
        if loaded is not None:
            matches.append(loaded)
    session.query(BacktestRunResult).filter(BacktestRunResult.run_id == run.id).delete()
    session.commit()
    for row in rows:
        _write_progress(run.id, row, 0, len(matches), "queued", None)
    reports = []
    for row in rows:
        try:
            predictor = load_predictor(row.artifact_path)
        except Exception as exc:
            logger.exception("Checkpoint %s could not be loaded", row.artifact_path)
            summary = _empty_report(row, config, "ARTIFACT_INVALID")
            _write_summary(run.id, row.id, summary)
            reports.append(summary)
            continue
        if not predictor.checkpoint_data:
            summary = _empty_report(row, config, "ARTIFACT_MISSING")
            _write_summary(run.id, row.id, summary)
            reports.append(summary)
            continue
        started = datetime.now(timezone.utc)
        _write_progress(run.id, row, 0, len(matches), "running", started)

        def on_match(done: int, total: int, model=row, started_at=started) -> None:
            _write_progress(run.id, model, done, total, "running", started_at)

        summary = ReplayRunner(predictor, config, sport).run(matches, builder, on_match=on_match)
        summary["model_version_id"] = str(row.id)
        summary["model_name"] = row.model_name
        summary["version_tag"] = row.version_tag
        summary["status"] = row.status
        _write_summary(run.id, row.id, summary)
        reports.append(summary)
    return reports


def _execute_strategy_search(session, run, request: Dict[str, Any]) -> None:
    """Rank stake gates. The run stores the report and does not write production bets."""
    from .strategy_job import prepare_windows, search_gates

    windows = prepare_windows(session)
    payload = search_gates(session, str(request["model_version_id"]), windows)
    model_id = uuid.UUID(str(request["model_version_id"]))
    _write_summary(run.id, model_id, {"kind": "strategy_search", "winner": payload.get("winner"), "baseline": payload.get("baseline")})


def _load_match(session, event, history, state_model, odds_model, decision_model):
    states = (
        session.query(state_model)
        .filter(state_model.event_id == event.id)
        .order_by(state_model.observed_at.asc())
        .all()
    )
    odds = (
        session.query(odds_model)
        .filter(odds_model.event_id == event.id)
        .order_by(odds_model.observed_at.asc())
        .all()
    )
    if not odds:
        return None
    decisions = (
        session.query(decision_model)
        .filter(decision_model.event_id == event.id)
        .order_by(decision_model.created_at.asc())
        .all()
    )
    names = (event.participant_a_name, event.participant_b_name)
    event_id = str(event.id)

    def history_features(moment):
        if history is None:
            return {}
        return history.features(names[0], names[1], moment, exclude_event_id=event_id)

    return SimpleNamespace(
        event_id=event_id,
        started_at=event.scheduled_start_at or event.created_at,
        states=states,
        odds=odds,
        decisions=decisions,
        history_features=history_features if history is not None else None,
        event=event,
    )


def _cached_by_event(sport: str, builder, event_ids: list) -> Optional[Dict[str, Any]]:
    from ..dataset_cache import read_event_matches

    version = str(getattr(builder, "feature_version", "") or "")
    if not version or not event_ids:
        return None
    try:
        loaded = read_event_matches(sport, version, event_ids)
    except Exception:
        logger.exception("Training tape for %s %s could not be read", sport, version)
        return None
    if loaded is None:
        return None
    matches, names = loaded
    found = {match.event_id: (match, names, version) for match in matches}
    logger.info("Backtest tape covers %d of %d %s matches", len(found), len(event_ids), sport)
    return found


def _load_cached_match(session, event, stored, odds_model):
    match, names, version = stored
    odds = (
        session.query(odds_model)
        .filter(odds_model.event_id == event.id)
        .order_by(odds_model.observed_at.asc())
        .all()
    )
    if not odds:
        return None
    return SimpleNamespace(
        event_id=str(event.id),
        started_at=event.scheduled_start_at or event.created_at,
        states=[],
        odds=odds,
        decisions=[],
        history_features=None,
        event=event,
        cached_rows=match.rows,
        cached_times=match.observed_at,
        cached_markets=match.row_markets or [],
        feature_names=names,
        feature_version=version,
    )


def _write_progress(run_id, row, done: int, total: int, state: str, started_at: Optional[datetime]) -> None:
    eta = None
    if state == "running" and started_at is not None and done > 0 and total > done:
        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
        eta = max(0, int(elapsed / done * (total - done)))
    summary = {
        "model_version_id": str(row.id),
        "model_name": row.model_name,
        "version_tag": row.version_tag,
        "sport_code": row.sport_code,
        "status": row.status,
        "progress": {
            "state": state,
            "matches_done": int(done),
            "matches_total": int(total),
            "eta_seconds": eta,
        },
    }
    _write_summary(run_id, row.id, summary)


def _write_summary(run_id, model_id, summary: Dict[str, Any]) -> None:
    from db.connection import SessionLocal
    from db.models.ml_registry import BacktestRun, BacktestRunResult

    with SessionLocal() as session:
        if session.get(BacktestRun, run_id) is None:
            return
        existing = (
            session.query(BacktestRunResult)
            .filter(BacktestRunResult.run_id == run_id, BacktestRunResult.model_version_id == model_id)
            .one_or_none()
        )
        if existing is None:
            session.add(
                BacktestRunResult(
                    run_id=run_id,
                    model_version_id=model_id,
                    summary=summary,
                    final_balance=float(summary.get("final_balance") or 0),
                    roi=summary.get("roi"),
                    bankruptcies=int(summary.get("bankruptcies") or 0),
                )
            )
        else:
            existing.summary = summary
            existing.final_balance = float(summary.get("final_balance") or 0)
            existing.roi = summary.get("roi")
            existing.bankruptcies = int(summary.get("bankruptcies") or 0)
        session.commit()


def _empty_report(row, config, reason: str) -> Dict[str, Any]:
    initial = float(config.get("initial_bankroll") or 5000)
    return {
        "model_version_id": str(row.id),
        "model_name": row.model_name,
        "version_tag": row.version_tag,
        "sport_code": row.sport_code,
        "status": row.status,
        "matches_seen": 0,
        "bets": 0,
        "wins": 0,
        "losses": 0,
        "voids": 0,
        "win_rate": None,
        "turnover": 0.0,
        "net_pnl": 0.0,
        "roi": None,
        "initial_bankroll": initial,
        "final_balance": initial,
        "min_balance": initial,
        "max_drawdown": 0.0,
        "bankruptcies": 0,
        "rejection_counts": {reason: 1},
        "gates_applied": [],
        "bet_records": [],
    }


def _first_job(entries) -> Optional[Dict[str, Any]]:
    import json

    if not entries:
        return None
    for _stream, messages in entries:
        for redis_id, fields in messages:
            raw_payload = fields.get("payload") or "{}"
            try:
                payload = json.loads(raw_payload) if raw_payload else {}
            except json.JSONDecodeError:
                payload = {}
            return {
                "redis_id": redis_id,
                "message_id": fields.get("message_id") or redis_id,
                "payload": payload,
            }
    return None


def _done_key(message_id: str) -> str:
    return f"neurobet:backtest-done:{message_id}"


def _fail_key(redis_id: str) -> str:
    return f"neurobet:backtest-fails:{redis_id}"
