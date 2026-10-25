import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import and_, asc, case, desc, func, or_, text

from sports_core import registry
try:
    import tennis_adapter
except ImportError:
    pass

try:
    import basketball_adapter
except ImportError:
    pass

try:
    import volleyball_adapter
except ImportError:
    pass

try:
    import hockey_adapter
except ImportError:
    pass

try:
    import football_adapter
except ImportError:
    pass


try:
    from data_quality import DataQualityEngine
    from db.connection import SessionLocal
    from db.models.events import Event, EventStateSnapshot
    from db.models.odds import Market, MarketSelection, OddsSnapshot
    from db.models.predictions import MLPrediction, FeatureSnapshot, LLMDecision, ModelBetJournal, ExternalPrediction, TennisForecastSnapshot
    from db.models.betting import (
        VirtualAccount, LedgerEntry, Bet, BetSettlement, BetProposal, BetValidationResult
    )
    from db.models.ml_registry import (
        ModelVersion,
        TrainingRun,
        TrainingDataset,
        ExperimentResult,
        BacktestRun,
        BacktestRunResult,
    )
    from db.models.research import WebResearchRun, WebDocument, WebEvidence
    from db.models.external_tennis import TennisPlayerProfile
    from db.models.external_tennis import TennisPlayerIdentityLink
    from db.models.external_tennis import TennisPointHistoryVersion, TennisPointRefreshJob
except ImportError:
    DataQualityEngine = None
    SessionLocal = None
    Event = None
    EventStateSnapshot = None
    Market = None
    MarketSelection = None
    OddsSnapshot = None
    MLPrediction = None
    TennisForecastSnapshot = None
    FeatureSnapshot = None
    LLMDecision = None
    ModelBetJournal = None
    VirtualAccount = None
    LedgerEntry = None
    Bet = None
    BetSettlement = None
    BetProposal = None
    BetValidationResult = None
    ModelVersion = None
    TrainingRun = None
    TrainingDataset = None
    ExperimentResult = None
    BacktestRun = None
    BacktestRunResult = None
    WebResearchRun = None
    WebDocument = None
    WebEvidence = None
    TennisPlayerIdentityLink = None
    TennisPlayerProfile = None
    TennisPointHistoryVersion = None
    TennisPointRefreshJob = None

try:
    from bankroll import (
        BankrollService, ReconciliationEngine, EventResultMatcher, BetSettlementEngine,
        CandidateSelector, CombinedDecisionPipeline, LLMStrategyComparator,
        CandidateScheduler, CandidateScorer, CooldownManager,
    )
    from contracts.decisions import DecisionPipelineConfig, CandidateItem, StrategyComparisonReport
    from contracts.scheduling import CandidateSchedulerConfig, CandidatePriorityItem, QueueStatusReport
    from sports_core import FonbetResultsParser
except ImportError:
    BankrollService = None
    ReconciliationEngine = None
    EventResultMatcher = None
    BetSettlementEngine = None
    CandidateSelector = None
    CombinedDecisionPipeline = None
    LLMStrategyComparator = None
    CandidateScheduler = None
    CandidateScorer = None
    CooldownManager = None
    DecisionPipelineConfig = None
    CandidateItem = None
    StrategyComparisonReport = None
    CandidateSchedulerConfig = None
    CandidatePriorityItem = None
    QueueStatusReport = None
    FonbetResultsParser = None

try:
    from contracts import (
        ShadowConfig,
        ShadowPredictionPair,
        DriftAnalysisReport,
        SegmentPerformance,
        ShadowEvaluationReport,
        ShadowEngine,
        OutcomePrediction,
        MLPrediction as MLPredictionContract,
    )
except ImportError:
    ShadowConfig = None
    ShadowPredictionPair = None
    DriftAnalysisReport = None
    SegmentPerformance = None
    ShadowEvaluationReport = None
    ShadowEngine = None
    OutcomePrediction = None
    MLPredictionContract = None

logger = logging.getLogger("backend")

app = FastAPI(
    title="Neurobet API",
    version="1.0.0",
    description="Research & Betting Simulation REST API for Neurobet (Primary Sport: Tennis)",
    root_path=os.getenv("BACKEND_ROOT_PATH", ""),
)

from .admin_confirmations import router as admin_confirmations_router
app.include_router(admin_confirmations_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    from observability import setup_observability, get_logger, REGISTRY, BANKROLL_BALANCE, BANKROLL_EXPOSURE
    setup_observability(app, service_name="backend")
    logger = get_logger("backend")
except ImportError:
    pass

# Global candidate scheduler instance for cost control & candidate queuing
global_candidate_scheduler = CandidateScheduler() if CandidateScheduler else None


@app.on_event("startup")
def startup_security_check():
    bet_mode = os.getenv("BET_MODE", "SIMULATION").upper()
    if bet_mode != "SIMULATION":
        logger.critical(
            "FATAL SECURITY VIOLATION: Unauthorized BET_MODE='%s'. System operates strictly in SIMULATION mode.",
            bet_mode,
        )
        raise RuntimeError(
            f"FATAL SECURITY VIOLATION: BET_MODE={bet_mode} is forbidden. System operates strictly in SIMULATION mode."
        )
    logger.info("Security hardening verified: BET_MODE=%s enforced.", bet_mode)


# --------------------------------------------------------------------------
# 1. Health & Readiness (Architecture Section 31)
# --------------------------------------------------------------------------
@app.get("/health")
@app.get("/api/health")
def health_check() -> Dict[str, Any]:
    """Returns application health, database connectivity, and server metadata."""
    db_connected = False
    if SessionLocal:
        try:
            with SessionLocal() as session:
                session.execute(text("SELECT 1"))
                db_connected = True
        except Exception as e:
            logger.warning(f"Database healthcheck failed: {e}")

    return {
        "status": "healthy" if db_connected else "degraded",
        "app": "neurobet-backend",
        "primary_sport": os.getenv("PRIMARY_SPORT", "tennis"),
        "database": "connected" if db_connected else "unavailable",
        "server_time_utc": datetime.now(timezone.utc).isoformat(),
        "timezone": "UTC",
        "version": "1.0.0",
    }


# --------------------------------------------------------------------------
# 2. Sports Endpoints
# --------------------------------------------------------------------------
@app.get("/api/sports")
def get_supported_sports() -> List[Dict[str, Any]]:
    """Returns all active sports registered in the generic sport adapter layer."""
    primary = os.getenv("PRIMARY_SPORT", "tennis").lower()
    sports = []
    active_sports = registry.list_sports() or ["tennis"]
    for sport_code in active_sports:
        desc = registry.get_descriptor(sport_code)
        d = desc.model_dump()
        d["code"] = sport_code
        d["sport_code"] = sport_code
        d["is_primary"] = (sport_code == primary)
        sports.append(d)
    return sports


@app.get("/api/sports/{sport_code}")
def get_sport_details(sport_code: str) -> Dict[str, Any]:
    """Returns sport descriptor and rules. Cleanly handles unsupported codes."""
    desc = registry.get_descriptor(sport_code)
    res = desc.model_dump()
    res["code"] = sport_code
    res["sport_code"] = sport_code
    primary = os.getenv("PRIMARY_SPORT", "tennis").lower()
    res["is_primary"] = (sport_code.lower() == primary)
    return res


# --------------------------------------------------------------------------
# 3. Events & Event Detail Endpoints
# --------------------------------------------------------------------------
@app.get("/api/events")
def get_events(
    sport_code: Optional[str] = None,
    status: Optional[str] = None,
    is_live: Optional[bool] = None,
    search: Optional[str] = None,
    include_synthetic: bool = False,
    with_bets: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """
    Returns paginated list of sporting events with filters for sport, status, live, and search.

    Fixture rows written by tests (source_event_id sim_evt_* and ext_*) stay in the
    database because bets may reference them, but they are omitted from the default list.
    """
    if not SessionLocal or not Event:
        return {"total": 0, "limit": limit, "offset": offset, "events": []}

    with SessionLocal() as session:
        q = session.query(Event)
        if sport_code:
            q = q.filter(Event.sport_code == sport_code.lower())
        if status:
            q = q.filter(Event.status == status.lower())
        if is_live is not None:
            q = q.filter(Event.is_live == is_live)
        if search:
            pattern = f"%{search}%"
            q = q.filter((Event.participant_a_name.ilike(pattern)) | (Event.participant_b_name.ilike(pattern)))
        if not include_synthetic:
            q = q.filter(
                ~Event.source_event_id.like("sim_evt_%"),
                ~Event.source_event_id.like("ext_%"),
                ~Event.source_event_id.like("evt_%"),
                ~Event.source_event_id.like("dr_%"),
            )
        if with_bets and Bet is not None and BankrollService is not None:
            account = BankrollService.get_or_create_account(session)
            placed_event_ids = session.query(Bet.event_id).filter(Bet.account_id == account.id)
            q = q.filter(Event.id.in_(placed_event_ids))

        total = q.count()
        events = q.order_by(Event.scheduled_start_at.desc()).offset(offset).limit(limit).all()
        from .quality_display import quality_by_event

        quality = quality_by_event(session, events)
        forecast_by_event = {}
        player_profiles = {}
        point_history_by_event = {}
        point_job_by_event = {}
        if events and TennisPointRefreshJob is not None:
            event_ids = [event.id for event in events]
            for job in session.query(TennisPointRefreshJob).filter(TennisPointRefreshJob.event_id.in_(event_ids)).all():
                point_job_by_event[str(job.event_id)] = job
        if events and TennisPointHistoryVersion is not None:
            event_ids = [event.id for event in events]
            ranked_points = session.query(
                TennisPointHistoryVersion.id.label("point_version_id"),
                func.row_number().over(partition_by=(TennisPointHistoryVersion.fonbet_event_id),
                    order_by=TennisPointHistoryVersion.retrieved_at.desc()).label("row_number"),
            ).filter(TennisPointHistoryVersion.fonbet_event_id.in_(event_ids)).subquery()
            point_versions = session.query(TennisPointHistoryVersion).join(ranked_points,
                ranked_points.c.point_version_id == TennisPointHistoryVersion.id).filter(ranked_points.c.row_number == 1).all()
            from db.models import TennisPointDocument
            source_by_document = {row.id: row.source for row in session.query(TennisPointDocument).filter(
                TennisPointDocument.id.in_([item.document_id for item in point_versions])
            ).all()} if point_versions else {}
            for item in point_versions:
                point_history_by_event[str(item.fonbet_event_id)] = {
                    "version": item,
                    "source": source_by_document.get(item.document_id, "unknown"),
                }
        if TennisPlayerProfile is not None and events:
            participant_ids = {
                participant_id
                for event in events
                for participant_id in (event.participant_a_id, event.participant_b_id)
                if participant_id is not None
            }
            for profile in session.query(TennisPlayerProfile).filter(TennisPlayerProfile.participant_id.in_(participant_ids)).all() if participant_ids else []:
                metrics = profile.metrics_json or {}
                player_profiles[str(profile.participant_id)] = {
                    "participant_id": str(profile.participant_id),
                    "canonical_player_id": str(profile.canonical_player_id) if profile.canonical_player_id else None,
                    "profile_url": profile.profile_url,
                    "status": profile.sync_status,
                    "error": profile.sync_error,
                    "metrics_version": profile.metrics_version,
                    "history_match_count": profile.history_match_count,
                    "available_at": profile.metrics_available_at.isoformat() if profile.metrics_available_at else None,
                    "source_watermark": profile.source_watermark.isoformat() if profile.source_watermark else None,
                    "last_full_history_scan_at": profile.last_full_history_scan_at.isoformat() if profile.last_full_history_scan_at else None,
                    "overall": metrics.get("overall", {}),
                    "recent": metrics.get("recent", {}),
                    "elo": metrics.get("elo", {}),
                    "glicko": metrics.get("glicko", {}),
                    "load": metrics.get("load", {}),
                    "coverage": metrics.get("coverage", {}),
                }
        if TennisForecastSnapshot is not None and events:
            event_ids = [event.id for event in events]
            ranked_forecasts = (
                session.query(
                    TennisForecastSnapshot.id.label("forecast_id"),
                    func.row_number().over(
                        partition_by=(TennisForecastSnapshot.event_id, TennisForecastSnapshot.model_name),
                        order_by=TennisForecastSnapshot.observed_at.desc(),
                    ).label("row_number"),
                )
                .filter(TennisForecastSnapshot.event_id.in_(event_ids))
                .subquery()
            )
            snapshots = (
                session.query(TennisForecastSnapshot)
                .join(ranked_forecasts, ranked_forecasts.c.forecast_id == TennisForecastSnapshot.id)
                .filter(ranked_forecasts.c.row_number <= 20)
                .order_by(TennisForecastSnapshot.observed_at.desc())
                .all()
            )
            for snapshot in snapshots:
                event_key = str(snapshot.event_id)
                forecast_by_event.setdefault(event_key, {}).setdefault(snapshot.model_name, []).append(snapshot.forecast_json)

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "events": [
                {
                    "id": str(e.id),
                    "sport_code": e.sport_code,
                    "source": e.source,
                    "source_event_id": e.source_event_id,
                    "participant_a": e.participant_a_name,
                    "participant_b": e.participant_b_name,
                    "status": e.status,
                    "is_live": e.is_live,
                    "current_score": e.current_score,
                    "current_period": e.current_period,
                    "scheduled_start_at": e.scheduled_start_at.isoformat() if e.scheduled_start_at else None,
                    "started_at": e.started_at.isoformat() if e.started_at else None,
                    "finished_at": e.finished_at.isoformat() if e.finished_at else None,
                    "quality_score": quality.get(e.id, {}).get(
                        "quality_score",
                        float(e.quality_score) if e.quality_score is not None else 100.0,
                    ),
                    "quality_reasons": quality.get(e.id, {}).get("quality_reasons", []),
                    "tennis_forecasts": forecast_by_event.get(str(e.id), {}),
                    "tennis_player_profiles": {
                        "a": player_profiles.get(str(e.participant_a_id)) if e.participant_a_id else None,
                        "b": player_profiles.get(str(e.participant_b_id)) if e.participant_b_id else None,
                    },
                    "point_history": ({
                        "source": point_history_by_event[str(e.id)]["source"],
                        "source_match_id": point_history_by_event[str(e.id)]["version"].source_match_id,
                        "retrieved_at": point_history_by_event[str(e.id)]["version"].retrieved_at.isoformat(),
                        "confirmed_points": (point_history_by_event[str(e.id)]["version"].quality_json or {}).get("confirmed_points", 0),
                        "gaps": (point_history_by_event[str(e.id)]["version"].quality_json or {}).get("gaps", []),
                        "complete": (point_history_by_event[str(e.id)]["version"].quality_json or {}).get("complete") is True,
                    } if str(e.id) in point_history_by_event else None),
                    "point_history_status": point_job_by_event[str(e.id)].status if str(e.id) in point_job_by_event else "not_queued",
                }
                for e in events
            ],
        }


@app.get("/api/tennis/events")
def get_tennis_events() -> List[Dict[str, Any]]:
    """Returns tennis events list for legacy frontend adapters."""
    res = get_events(sport_code="tennis", limit=50, offset=0)
    return res.get("events", [])


# --------------------------------------------------------------------------
# Data Quality Endpoints
# --------------------------------------------------------------------------
@app.get("/api/quality/report")
def get_data_quality_report() -> Dict[str, Any]:
    """Returns global historical data quality audit report."""
    if not DataQualityEngine or not SessionLocal:
        return {"error": "Data quality engine unavailable"}
    with SessionLocal() as session:
        report = DataQualityEngine.run_full_audit(session)
        return report.model_dump()


@app.get("/api/quality/events/{event_id}")
def get_event_quality_summary(event_id: str) -> Dict[str, Any]:
    """Returns data quality score and audit issues for a specific event."""
    if not DataQualityEngine or not SessionLocal:
        return {"error": "Data quality engine unavailable"}
    with SessionLocal() as session:
        try:
            summary = DataQualityEngine.audit_event(session, event_id)
            return summary.model_dump()
        except ValueError as e:
            return {"error": str(e), "quality_score": 0.0, "issues": []}



@app.get("/api/events/{event_id}")
def get_event_detail(event_id: str) -> Dict[str, Any]:
    """Returns full details of an event including metadata and quality score."""
    if not SessionLocal or not Event:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        e_uuid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event UUID")

    with SessionLocal() as session:
        evt = session.query(Event).filter(Event.id == e_uuid).first()
        if not evt:
            raise HTTPException(status_code=404, detail="Event not found")

        from .quality_display import quality_by_event

        shown = quality_by_event(session, [evt]).get(evt.id, {})
        return {
            "id": str(evt.id),
            "sport_code": evt.sport_code,
            "source": evt.source,
            "source_event_id": evt.source_event_id,
            "participant_a": evt.participant_a_name,
            "participant_b": evt.participant_b_name,
            "status": evt.status,
            "is_live": evt.is_live,
            "current_score": evt.current_score,
            "current_period": evt.current_period,
            "scheduled_start_at": evt.scheduled_start_at.isoformat() if evt.scheduled_start_at else None,
            "started_at": evt.started_at.isoformat() if evt.started_at else None,
            "finished_at": evt.finished_at.isoformat() if evt.finished_at else None,
            "quality_score": shown.get(
                "quality_score",
                float(evt.quality_score) if evt.quality_score is not None else 100.0,
            ),
            "quality_reasons": shown.get("quality_reasons", []),
            "metadata_json": evt.metadata_json,
        }


# --------------------------------------------------------------------------
# 4. Timeline & Odds History (TimescaleDB Hypertables)
# --------------------------------------------------------------------------
@app.get("/api/events/{event_id}/timeline")
def get_event_timeline(event_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Returns chronological match state timeline snapshots for an event."""
    if not SessionLocal or not EventStateSnapshot:
        return []

    try:
        e_uuid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event UUID")

    with SessionLocal() as session:
        snapshots = (
            session.query(EventStateSnapshot)
            .filter(EventStateSnapshot.event_id == e_uuid)
            .order_by(EventStateSnapshot.observed_at.asc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": str(s.id),
                "observed_at": s.observed_at.isoformat(),
                "status": s.status,
                "current_period": s.current_period,
                "score": s.score,
                "score_detail": s.score_detail,
                "match_clock_seconds": s.match_clock_seconds,
            }
            for s in snapshots
        ]


@app.get("/api/events/{event_id}/forecasts")
def get_event_tennis_forecasts(
    event_id: str,
    model_name: Optional[str] = None,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """Return odds-free forecast history, newest first."""
    if not SessionLocal or not TennisForecastSnapshot:
        return {"total": 0, "forecasts": []}
    try:
        event_uuid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event UUID")
    with SessionLocal() as session:
        query = session.query(TennisForecastSnapshot).filter(TennisForecastSnapshot.event_id == event_uuid)
        if model_name:
            query = query.filter(TennisForecastSnapshot.model_name == model_name)
        total = query.count()
        rows = query.order_by(
            TennisForecastSnapshot.observed_at.desc(), TennisForecastSnapshot.id.desc()
        ).offset(offset).limit(limit).all()
        return {"total": total, "limit": limit, "offset": offset, "forecasts": [row.forecast_json for row in rows]}


@app.get("/api/events/{event_id}/forecasts/latest")
def get_latest_tennis_forecast(event_id: str, model_name: Optional[str] = None) -> Dict[str, Any]:
    """Return the newest odds-free forecast, optionally for one candidate."""
    if not SessionLocal or not TennisForecastSnapshot:
        return {"forecast": None}
    try:
        event_uuid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event UUID")
    with SessionLocal() as session:
        query = session.query(TennisForecastSnapshot).filter(TennisForecastSnapshot.event_id == event_uuid)
        if model_name:
            query = query.filter(TennisForecastSnapshot.model_name == model_name)
        row = query.order_by(
            TennisForecastSnapshot.observed_at.desc(), TennisForecastSnapshot.id.desc()
        ).first()
        return {"forecast": row.forecast_json if row else None}


def _point_history_provenance(selected_versions, source_by_document: dict[Any, str]) -> dict[str, Any]:
    """Describe the provider and completeness of selected point-history versions."""
    coverage: dict[str, dict[str, Any]] = {}
    for version in selected_versions:
        source = source_by_document.get(version.document_id, "unknown")
        bucket = coverage.setdefault(source, {
            "documents": 0, "complete": True, "gaps": 0, "last_available_at": None,
        })
        quality = version.quality_json if isinstance(version.quality_json, dict) else {}
        bucket["documents"] += 1
        bucket["complete"] = bucket["complete"] and quality.get("complete") is True
        bucket["gaps"] += len(quality.get("gaps") or [])
        available = version.available_at.isoformat() if version.available_at else None
        if available and (bucket["last_available_at"] is None or available > bucket["last_available_at"]):
            bucket["last_available_at"] = available
    sources = sorted(coverage)
    return {
        "source": (source_by_document.get(max(
            selected_versions, key=lambda row: (row.available_at, str(row.id))
        ).document_id, "unknown") if selected_versions else "flashscore"),
        "sources": sources,
        "source_coverage": coverage,
        "complete": bool(coverage) and all(item["complete"] for item in coverage.values()),
    }


@app.get("/api/events/{event_id}/point-history")
def get_event_point_history(
    event_id: str, as_of: Optional[datetime] = None, limit: int = 500, offset: int = 0,
    include_versions: bool = False,
) -> Dict[str, Any]:
    """Return archived, point-in-time point history with its actual provider provenance."""
    from db.connection import SessionLocal
    from db.models import (
        TennisPointDocument, TennisPointHistoryVersion, TennisPointMatchLink,
        TennisPointRefreshJobEvent,
    )
    from tennis_adapter.point_history import is_authoritative_point_history_version
    try:
        event_uuid = uuid.UUID(event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid event_id") from exc
    limit, offset = max(1, min(int(limit), 2000)), max(0, int(offset))
    with SessionLocal() as session:
        resolved_as_of = as_of or datetime.now(timezone.utc)
        resolved_as_of = resolved_as_of.replace(tzinfo=timezone.utc) if resolved_as_of.tzinfo is None else resolved_as_of.astimezone(timezone.utc)
        event_links = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.fonbet_event_id == event_uuid,
            TennisPointMatchLink.available_at <= resolved_as_of,
        ).all()
        candidate_source_ids = list({link.source_match_id for link in event_links})
        all_link_rows = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.source_match_id.in_(candidate_source_ids) if candidate_source_ids else False,
            TennisPointMatchLink.fonbet_event_id.is_not(None),
            TennisPointMatchLink.available_at <= resolved_as_of,
        ).order_by(TennisPointMatchLink.available_at, TennisPointMatchLink.id).all()
        # Resolve the latest assignment for a source match globally before
        # interpreting its status, so reassignment cannot leave two active events.
        latest_links = {}
        for link in all_link_rows:
            latest_links[link.source_match_id] = link
        valid_links = [link for link in latest_links.values()
                       if link.fonbet_event_id == event_uuid and link.status in ("matched", "manual")]
        source_ids = list(dict.fromkeys(link.source_match_id for link in valid_links))
        versions = session.query(TennisPointHistoryVersion).filter(
            TennisPointHistoryVersion.source_match_id.in_(source_ids) if source_ids else False,
            TennisPointHistoryVersion.available_at.is_not(None),
            TennisPointHistoryVersion.available_at <= resolved_as_of,
        ).order_by(TennisPointHistoryVersion.available_at, TennisPointHistoryVersion.id).all()
        documents = session.query(TennisPointDocument).filter(
            TennisPointDocument.id.in_([row.document_id for row in versions]) if versions else False
        ).all()
        source_by_document = {row.id: row.source for row in documents}
        latest_by_source_set = {}
        for version in versions:
            if not is_authoritative_point_history_version(version.events_json, version.quality_json):
                continue
            # Persisted set_number also represents an empty/removed set document,
            # so an explicitly marked empty correction can tombstone an older
            # parsed sequence. Fetch/parse failures are skipped above.
            set_numbers = {version.quality_json.get("set_number")} if isinstance(version.quality_json, dict) and version.quality_json.get("set_number") else set()
            set_numbers.update(item.get("set") for item in (version.events_json or []) if item.get("set") is not None)
            for set_no in set_numbers or {None}:
                latest_by_source_set[(version.source_match_id, set_no)] = version
        selected_versions = list(latest_by_source_set.items())
        newest = max((row for _, row in selected_versions), key=lambda row: (row.available_at, str(row.id)), default=None)
        provenance = _point_history_provenance(
            [row for _, row in selected_versions], source_by_document
        )
        sequence = []
        for (source_match_id, set_no), version in selected_versions:
            for index, item in enumerate(version.events_json or []):
                if set_no is not None and item.get("set") != set_no:
                    continue
                sequence.append({**item, "source_match_id": version.source_match_id,
                    "set_order": item.get("set") if item.get("set") is not None else 0,
                    "order": index, "document_id": str(version.document_id),
                    "retrieved_at": version.retrieved_at.isoformat(),
                    "parsed_at": version.parsed_at.isoformat() if version.parsed_at else None,
                    "available_at": version.available_at.isoformat() if version.available_at else None,
                    "parser_version": version.parser_version, "retrospective": version.retrospective})
        sequence.sort(key=lambda item: (item["set_order"], item["source_match_id"], item["order"], item["available_at"] or ""))
        total = len(sequence)
        page = sequence[offset:offset + limit]
        job_event = session.query(TennisPointRefreshJobEvent).filter(
            TennisPointRefreshJobEvent.event_id == event_uuid,
            TennisPointRefreshJobEvent.available_at <= resolved_as_of,
        ).order_by(TennisPointRefreshJobEvent.available_at.desc(), TennisPointRefreshJobEvent.id.desc()).first()
        selected_quality = [version.quality_json or {} for _, version in selected_versions]
        gaps = [gap for quality in selected_quality for gap in quality.get("gaps", [])]
        versions_payload = None
        if include_versions:
            versions_payload = [{"source_match_id": row.source_match_id,
                "source": source_by_document.get(row.document_id, "unknown"),
                "document_id": str(row.document_id), "retrieved_at": row.retrieved_at.isoformat(),
                "parsed_at": row.parsed_at.isoformat() if row.parsed_at else None,
                "available_at": row.available_at.isoformat() if row.available_at else None,
                "availability_status": row.availability_status,
                "parser_version": row.parser_version, "retrospective": row.retrospective,
                "quality": row.quality_json or {}}
                for row in versions]
        return {
            "event_id": event_id, "as_of": resolved_as_of.isoformat(), "resolved_as_of": resolved_as_of.isoformat(),
            **provenance,
            "source_match_id": newest.source_match_id if newest else (source_ids[-1] if source_ids else None),
            "versions_total": len(versions), "total": total, "limit": limit, "offset": offset,
            "sequence": page, "versions": versions_payload,
            "freshness_seconds": max(0.0, (resolved_as_of-newest.available_at).total_seconds()) if newest else None,
            "collection_status": job_event.status if job_event else "not_queued",
            "last_error": job_event.last_error if job_event else None,
            "incompleteness_reasons": gaps if selected_versions else ["point_history_unavailable"],
            "confirmed_points": sum(bool(item.get("confirmed")) for item in sequence) if selected_versions else None,
        }


@app.post("/api/events/{event_id}/point-history/link")
def resolve_event_point_history_link(event_id: str, payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Manually confirm an ambiguous external match link for an event."""
    from .admin_auth import require_admin
    require_admin(request)
    from db.connection import SessionLocal
    from db.models import Event, ExtTennisMatch, TennisMatchLookupJob, TennisPlayerProfile, TennisPointMatchLink, TennisPointRefreshJob, TennisPointRefreshJobEvent
    source_match_id = str(payload.get("source_match_id") or "").strip()
    if not source_match_id:
        raise HTTPException(status_code=422, detail="source_match_id is required")
    try:
        event_uuid = uuid.UUID(event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid event_id") from exc
    with SessionLocal() as session:
        event = session.get(Event, event_uuid)
        if event is None or event.sport_code != "tennis":
            raise HTTPException(status_code=404, detail="Tennis event not found")
        links = session.query(TennisPointMatchLink).filter_by(source_match_id=source_match_id).order_by(
            TennisPointMatchLink.available_at.desc(), TennisPointMatchLink.id.desc()
        ).all()
        if not links:
            raise HTTPException(status_code=404, detail="No saved candidate link")
        now = datetime.now(timezone.utc)
        latest_link = links[0]
        if latest_link.fonbet_event_id not in (None, event_uuid) and latest_link.status in {"manual", "matched"}:
            raise HTTPException(status_code=409, detail="Source match is already linked to another event")
        external = session.query(ExtTennisMatch).filter_by(source="flashscore", source_match_id=source_match_id).order_by(
            ExtTennisMatch.available_at.desc()
        ).first()
        a_profile = session.get(TennisPlayerProfile, event.participant_a_id) if event.participant_a_id else None
        b_profile = session.get(TennisPlayerProfile, event.participant_b_id) if event.participant_b_id else None
        source_a = str((a_profile.source_identities or {}).get("flashscore") or "") if a_profile else ""
        source_b = str((b_profile.source_identities or {}).get("flashscore") or "") if b_profile else ""
        if external and source_a and source_b:
            if external.home_player_id == source_a and external.away_player_id == source_b:
                external_home_is_a = True
            elif external.home_player_id == source_b and external.away_player_id == source_a:
                external_home_is_a = False
            else:
                raise HTTPException(status_code=422, detail="Flashscore player IDs do not match the event participants")
        elif external and external.home_name.casefold() == event.participant_a_name.casefold() and external.away_name.casefold() == event.participant_b_name.casefold():
            external_home_is_a = True
        elif external and external.home_name.casefold() == event.participant_b_name.casefold() and external.away_name.casefold() == event.participant_a_name.casefold():
            external_home_is_a = False
        else:
            external_home_is_a = payload.get("external_home_is_fonbet_a")
            if external_home_is_a not in (True, False):
                raise HTTPException(status_code=422, detail="Cannot verify side order; provide external_home_is_fonbet_a")
        session.add(TennisPointMatchLink(
            source_match_id=source_match_id,
            fonbet_event_id=event_uuid,
            status="manual",
            confidence=1.0,
            evidence_json={"resolution": "manual_confirmation", "event_id": event_id,
                           "external_home_is_fonbet_a": external_home_is_a,
                           "previous_link_id": str(latest_link.id)},
            available_at=now,
        ))
        lookup = session.get(TennisMatchLookupJob, event_uuid, with_for_update=True)
        if lookup:
            lookup.status = "matched"; lookup.source_match_id = source_match_id
            lookup.last_error = None; lookup.next_run_at = now; lookup.updated_at = now
            lookup.attempt_token = None; lookup.lease_until = None
        point_job = session.get(TennisPointRefreshJob, event_uuid, with_for_update=True)
        if point_job:
            point_job.status = "queued"; point_job.last_error = None; point_job.next_run_at = now
            point_job.last_requested_at = now; point_job.updated_at = now
            session.add(TennisPointRefreshJobEvent(event_id=event_uuid, status="queued", last_error=None,
                source_match_id=source_match_id, available_at=now))
        session.commit()
        return {"status": "linked", "event_id": event_id, "source_match_id": source_match_id}


@app.get("/api/tennis-history/players/{participant_id}")
def get_tennis_player_metrics(participant_id: str) -> Dict[str, Any]:
    """Return the current cached tennis metrics for a Fonbet participant."""
    if not SessionLocal or not TennisPlayerProfile:
        raise HTTPException(status_code=503, detail="Tennis player metrics are unavailable")
    try:
        participant_uuid = uuid.UUID(participant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid participant UUID")
    with SessionLocal() as session:
        profile = session.get(TennisPlayerProfile, participant_uuid)
        if profile is None:
            raise HTTPException(status_code=404, detail="Tennis participant profile not found")
        links = session.query(TennisPlayerIdentityLink).filter_by(
            participant_id=participant_uuid, source="flashscore"
        ).order_by(TennisPlayerIdentityLink.available_at.desc(), TennisPlayerIdentityLink.id.desc()).all()
        latest_link = links[0] if links else None
        identities = profile.source_identities or {}
        confirmed = bool(
            latest_link
            and latest_link.status == "confirmed"
            and identities.get("flashscore") in {None, latest_link.source_player_id}
        )
        has_unconfirmed_source = bool(
            identities.get("flashscore")
            or identities.get("flashscore_candidates")
            or profile.profile_url
            or latest_link
        ) and not confirmed
        return {
            "participant_id": str(profile.participant_id),
            "canonical_player_id": str(profile.canonical_player_id) if profile.canonical_player_id else None,
            "source_identities": profile.source_identities,
            "profile_url": profile.profile_url,
            "status": "identity_confirmation_required" if has_unconfirmed_source else profile.sync_status,
            "error": "FLASHScore_PROFILE_REQUIRES_CONFIRMATION" if has_unconfirmed_source else profile.sync_error,
            "metrics_version": profile.metrics_version if not has_unconfirmed_source else 0,
            "history_match_count": profile.history_match_count if not has_unconfirmed_source else 0,
            "available_at": profile.metrics_available_at.isoformat() if profile.metrics_available_at and not has_unconfirmed_source else None,
            "history_digest": profile.history_digest if not has_unconfirmed_source else None,
            "metrics": profile.metrics_json if not has_unconfirmed_source else {},
        }


@app.get("/api/events/{event_id}/odds")
def get_event_odds_history(
    event_id: str,
    market_type: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Returns chronological odds movement history for an event."""
    if not SessionLocal or not OddsSnapshot:
        return []

    try:
        e_uuid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event UUID")

    with SessionLocal() as session:
        q = session.query(OddsSnapshot).filter(OddsSnapshot.event_id == e_uuid)
        if market_type:
            q = q.filter(OddsSnapshot.market_type == market_type)

        snapshots = q.order_by(OddsSnapshot.observed_at.asc()).limit(limit).all()
        return [
            {
                "id": str(o.id),
                "observed_at": o.observed_at.isoformat(),
                "market_type": o.market_type,
                "outcome": o.outcome,
                "line_value": float(o.line_value) if o.line_value is not None else None,
                "odds": float(o.odds),
                "probability_implied": float(o.probability_implied),
            }
            for o in snapshots
        ]


def _public_composite(detail: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """One visible model state. Inner lines stay inside this object."""
    raw = (detail or {}).get("composite") if isinstance(detail, dict) else None
    if not isinstance(raw, dict):
        return None
    components = []
    for item in raw.get("components") or []:
        if not isinstance(item, dict):
            continue
        confirmed = bool(item.get("confirmed"))
        reason = str(item.get("reason") or "")
        side = item.get("confirmed_side") or item.get("side")
        if confirmed and side:
            verdict = f"подтвердила {side}"
        elif reason:
            verdict = reason
        elif side:
            verdict = f"смотрит {side}"
        else:
            verdict = "ещё нет"
        components.append(
            {
                "component_id": str(item.get("id") or item.get("component_id") or ""),
                "label": str(item.get("label") or ""),
                "side": side,
                "confirmed": confirmed,
                "probability": float(item.get("probability") or 0.0),
                "edge": float(item.get("edge") or 0.0),
                "reason": reason,
                "verdict": verdict,
            }
        )
    selected = bool(raw.get("selected"))
    return {
        "schema_version": "1.0.0",
        "label": raw.get("label") or "Составная модель",
        "phase": raw.get("phase") or "0/3",
        "display_phase": raw.get("display_phase") or ("Выбрано" if selected else raw.get("phase") or "0/3"),
        "selected": selected,
        "market": raw.get("market") or "",
        "side": raw.get("side"),
        "agreement_count": int(raw.get("agreement_count") or 0),
        "components": components,
    }


# --------------------------------------------------------------------------
# 5. ML Predictions & Web Research
# --------------------------------------------------------------------------
@app.get("/api/predictions")
@app.get("/api/events/{event_id}/predictions")
def get_predictions(event_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Returns numeric ML predictions, edge, and confidence scores."""
    if not SessionLocal or not MLPrediction:
        return []

    with SessionLocal() as session:
        q = session.query(MLPrediction)
        if event_id:
            try:
                q = q.filter(MLPrediction.event_id == uuid.UUID(event_id))
            except ValueError:
                return []

        preds = q.order_by(MLPrediction.created_at.desc()).limit(limit).all()
        return [
            {
                "id": str(p.id),
                "event_id": str(p.event_id),
                "sport_code": p.sport_code,
                "model_version": p.model_version,
                "market": p.market,
                "predicted_outcome": p.predicted_outcome,
                "model_probability": float(p.model_probability),
                "bookmaker_odds": float(p.bookmaker_odds),
                "fair_odds": float(p.fair_odds),
                "edge": float(p.edge),
                "confidence": float(p.confidence),
                "has_positive_edge": p.has_positive_edge,
                "decision_eligible": (p.outcomes_detail or {}).get("decision_eligible", p.has_positive_edge),
                "decision_reason": str((p.outcomes_detail or {}).get("decision_reason") or ""),
                "is_shadow": bool(p.is_shadow),
                "model_name": str((p.outcomes_detail or {}).get("model_name") or ""),
                "composite": _public_composite(p.outcomes_detail),
                "created_at": p.created_at.isoformat(),
            }
            for p in preds
        ]


@app.get("/api/model-bets")
def list_model_bets(
    event_id: Optional[str] = None,
    sport_code: Optional[str] = None,
    model_name: Optional[str] = None,
    is_shadow: Optional[bool] = None,
    market: Optional[str] = None,
    result_status: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Statistical journal of model sides, including shadows and decisions with no stake."""
    if not SessionLocal or not ModelBetJournal or not Event:
        return []
    with SessionLocal() as session:
        query = session.query(ModelBetJournal, Event).join(Event, Event.id == ModelBetJournal.event_id)
        if event_id:
            try:
                query = query.filter(ModelBetJournal.event_id == uuid.UUID(event_id))
            except ValueError:
                return []
        if sport_code:
            query = query.filter(ModelBetJournal.sport_code == sport_code)
        if model_name:
            query = query.filter(ModelBetJournal.model_name == model_name)
        if is_shadow is not None:
            query = query.filter(ModelBetJournal.is_shadow.is_(is_shadow))
        if market:
            query = query.filter(ModelBetJournal.market == market)
        if result_status:
            query = query.filter(ModelBetJournal.result_status == result_status)
        rows = query.order_by(ModelBetJournal.decided_at.desc()).limit(min(limit, 500)).all()
        return [
            {
                "id": str(row.id),
                "event_id": str(row.event_id),
                "sport_code": row.sport_code,
                "participant_a": event.participant_a_name,
                "participant_b": event.participant_b_name,
                "model_name": row.model_name,
                "model_version": row.model_version,
                "is_shadow": bool(row.is_shadow),
                "market": row.market,
                "outcome": row.outcome,
                "model_probability": float(row.model_probability),
                "bookmaker_odds": float(row.bookmaker_odds),
                "fair_odds": float(row.fair_odds),
                "edge": float(row.edge),
                "expected_value": float(row.expected_value),
                "confidence": float(row.confidence),
                "stake_amount": float(row.stake_amount),
                "reason": row.reason,
                "detail": row.detail or {},
                "decided_at": row.decided_at.isoformat(),
                "result_status": row.result_status,
                "result_winner": row.result_winner,
                "settled_at": row.settled_at.isoformat() if row.settled_at else None,
            }
            for row, event in rows
        ]


# Known fixture / seed snippet that was duplicated across unrelated events.
_FIXTURE_RESEARCH_SNIPPETS = frozenset({
    "rune reported feeling 100% physically recovered.",
})


@app.get("/api/research")
@app.get("/api/events/{event_id}/research")
def get_research_evidence(event_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Returns qualitative research evidence snippets extracted for matches."""
    if not SessionLocal or not WebEvidence:
        return []

    with SessionLocal() as session:
        if WebDocument is not None:
            q = session.query(WebEvidence, WebDocument).outerjoin(
                WebDocument, WebEvidence.document_id == WebDocument.id
            )
        else:
            q = session.query(WebEvidence)

        if event_id:
            try:
                q = q.filter(WebEvidence.event_id == uuid.UUID(event_id))
            except ValueError:
                return []

        # Fetch extra rows so fixture filtering still leaves up to `limit` items.
        rows = q.order_by(WebEvidence.created_at.desc()).limit(max(limit * 3, limit)).all()
        results: List[Dict[str, Any]] = []
        for row in rows:
            if WebDocument is not None:
                e, doc = row
            else:
                e, doc = row, None
            snippet = (e.snippet or "").strip()
            if snippet.lower() in _FIXTURE_RESEARCH_SNIPPETS:
                continue
            results.append(
                {
                    "id": str(e.id),
                    "event_id": str(e.event_id),
                    "snippet": e.snippet,
                    "title": doc.title if doc else None,
                    "url": doc.url if doc else None,
                    "source_domain": doc.source_domain if doc else None,
                    "source_title": doc.title if doc else None,
                    "relevance_score": float(e.relevance_score),
                    "freshness_score": float(e.freshness_score),
                    "created_at": e.created_at.isoformat(),
                }
            )
            if len(results) >= limit:
                break
        return results


@app.get("/api/events/{event_id}/llm")
def get_event_llm_decisions(event_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Returns latest persisted LLM qualitative verdicts for a single event."""
    if not SessionLocal or not LLMDecision:
        return []

    try:
        e_uuid = uuid.UUID(event_id)
    except ValueError:
        return []

    with SessionLocal() as session:
        decisions = (
            session.query(LLMDecision)
            .filter(LLMDecision.event_id == e_uuid)
            .order_by(LLMDecision.created_at.desc())
            .limit(limit)
            .all()
        )
        results: List[Dict[str, Any]] = []
        for d in decisions:
            raw = d.raw_json if isinstance(d.raw_json, dict) else {}
            results.append(
                {
                    "id": str(d.id),
                    "event_id": str(d.event_id),
                    "llm_version": d.llm_version,
                    "verdict": d.verdict,
                    "confidence_adjustment": float(d.confidence_adjustment),
                    "injury_risk": d.injury_risk,
                    "fatigue_risk": d.fatigue_risk,
                    "reasoning": d.reasoning,
                    "summary": raw.get("summary") or d.reasoning,
                    "reason_codes": raw.get("reason_codes") or [],
                    "contradictions": raw.get("contradictions") or [],
                    "research_quality": raw.get("research_quality"),
                    "model_identifier": raw.get("model_identifier"),
                    "prompt_version": raw.get("prompt_version"),
                    "raw_json": raw,
                    "created_at": d.created_at.isoformat(),
                }
            )
        return results


@app.get("/api/predictions/external")
def list_external_predictions(
    sport_code: Optional[str] = None,
    status: Optional[str] = None,
    event_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Returns external pre-match expert predictions aggregated from analytical websites."""
    if not SessionLocal or not ExternalPrediction:
        return {"total": 0, "predictions": []}

    with SessionLocal() as session:
        q = session.query(ExternalPrediction)
        if sport_code:
            q = q.filter(ExternalPrediction.sport_code == sport_code)
        if status:
            q = q.filter(ExternalPrediction.status == status)
        if event_id:
            try:
                q = q.filter(ExternalPrediction.matched_event_id == uuid.UUID(event_id))
            except ValueError:
                return {"total": 0, "predictions": []}

        total = q.count()
        rows = q.order_by(ExternalPrediction.created_at.desc()).offset(offset).limit(min(limit, 200)).all()

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "predictions": [
                {
                    "id": str(r.id),
                    "sport_code": r.sport_code,
                    "source_code": r.source_code,
                    "source_url": r.source_url,
                    "tournament_name": r.tournament_name,
                    "author_name": r.author_name,
                    "author_roi": float(r.author_roi) if r.author_roi is not None else None,
                    "author_winrate": float(r.author_winrate) if r.author_winrate is not None else None,
                    "raw_participant_a": r.raw_participant_a,
                    "raw_participant_b": r.raw_participant_b,
                    "canonical_participant_a": r.canonical_participant_a,
                    "canonical_participant_b": r.canonical_participant_b,
                    "market_type": r.market_type,
                    "predicted_outcome": r.predicted_outcome,
                    "raw_prediction_text": r.raw_prediction_text,
                    "recommended_odds": float(r.recommended_odds) if r.recommended_odds is not None else None,
                    "reasoning_snippet": r.reasoning_snippet,
                    "estimated_start_at": r.estimated_start_at.isoformat() if r.estimated_start_at else None,
                    "matched_event_id": str(r.matched_event_id) if r.matched_event_id else None,
                    "matched_at": r.matched_at.isoformat() if r.matched_at else None,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                }
                for r in rows
            ],
        }


@app.get("/api/predictions/consensus/{event_id}")
def get_prediction_consensus(event_id: str) -> Dict[str, Any]:
    """Returns consensus summary across all external predictions for an event."""
    if not SessionLocal or not ExternalPrediction:
        return {"event_id": event_id, "total_predictions": 0, "consensus_pick": "neutral"}

    try:
        ev_uuid = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event_id UUID")

    with SessionLocal() as session:
        preds = session.query(ExternalPrediction).filter(
            ExternalPrediction.matched_event_id == ev_uuid
        ).all()

        if not preds:
            return {
                "event_id": event_id,
                "sport_code": "tennis",
                "total_predictions": 0,
                "p1_count": 0,
                "p2_count": 0,
                "p1_share": 0.0,
                "p2_share": 0.0,
                "consensus_pick": "neutral",
                "consensus_confidence": 0.0,
                "sources": [],
                "expert_snippets": [],
            }

        total = len(preds)
        p1_count = sum(1 for p in preds if (p.predicted_outcome or "").lower() in ("p1", "player_a", "1"))
        p2_count = sum(1 for p in preds if (p.predicted_outcome or "").lower() in ("p2", "player_b", "2"))
        other_count = total - (p1_count + p2_count)

        p1_share = p1_count / total if total > 0 else 0.0
        p2_share = p2_count / total if total > 0 else 0.0

        p1_odds = [float(p.recommended_odds) for p in preds if (p.predicted_outcome or "").lower() in ("p1", "player_a", "1") and p.recommended_odds]
        p2_odds = [float(p.recommended_odds) for p in preds if (p.predicted_outcome or "").lower() in ("p2", "player_b", "2") and p.recommended_odds]

        consensus_pick = "neutral"
        if p1_share >= 0.60:
            consensus_pick = "p1"
        elif p2_share >= 0.60:
            consensus_pick = "p2"

        volume_factor = min(1.0, total / 5.0)
        unanimity = max(p1_share, p2_share)
        confidence = round(volume_factor * unanimity, 4)

        sources = sorted(list({p.source_code for p in preds}))
        snippets = [f"[{p.source_code}] {p.reasoning_snippet}" for p in preds if p.reasoning_snippet][:5]

        return {
            "event_id": event_id,
            "sport_code": "tennis",
            "total_predictions": total,
            "p1_count": p1_count,
            "p2_count": p2_count,
            "other_count": other_count,
            "p1_share": round(p1_share, 4),
            "p2_share": round(p2_share, 4),
            "avg_p1_odds": round(sum(p1_odds) / len(p1_odds), 4) if p1_odds else None,
            "avg_p2_odds": round(sum(p2_odds) / len(p2_odds), 4) if p2_odds else None,
            "consensus_pick": consensus_pick,
            "consensus_confidence": confidence,
            "sources": sources,
            "expert_snippets": snippets,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


@app.post("/api/research/run")
def trigger_research_run(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Triggers or records on-demand web research run for an event."""
    if not SessionLocal or not WebResearchRun:
        return {"error": "Research unavailable"}

    raw_event_id = payload.get("event_id")
    query_text = payload.get("query", "tennis match analysis")
    if not raw_event_id:
        raise HTTPException(status_code=400, detail="event_id is required")

    try:
        e_uuid = uuid.UUID(str(raw_event_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event UUID")

    with SessionLocal() as session:
        run = WebResearchRun(
            id=uuid.uuid4(),
            event_id=e_uuid,
            query=query_text,
            status="completed",
            documents_found=1,
        )
        session.add(run)
        session.commit()
        return {
            "status": "success",
            "run_id": str(run.id),
            "event_id": str(run.event_id),
            "query": run.query,
        }


# --------------------------------------------------------------------------
# 6. Virtual Bankroll, Ledger, Bets & Proposals
# --------------------------------------------------------------------------
@app.get("/api/account")
@app.get("/api/bankroll")
def get_bankroll_summary() -> Dict[str, Any]:
    """Returns virtual bankroll account summary (balance, exposure, equity)."""
    if not BankrollService or not SessionLocal:
        return {"error": "Bankroll service unavailable"}
    with SessionLocal() as session:
        account = BankrollService.get_or_create_account(session)
        summary = BankrollService.get_account_summary(session, account.id)
        summary["balance"] = summary.get("available_balance", 0.0)
        try:
            BANKROLL_BALANCE.set(float(summary.get("available_balance", 0.0)))
            BANKROLL_EXPOSURE.set(float(summary.get("locked_exposure", 0.0)))
        except Exception:
            pass
        session.commit()
        return summary


def _combat_event_ids(column):
    """Real Fonbet event ids are numeric. Fixture ids use sim_evt_, ext_, evt_, dr_."""
    return column.op("~")(r"^[0-9]+$")


@app.get("/api/ledger")
@app.get("/api/bankroll/ledger")
def get_ledger_entries(
    limit: int = 50,
    offset: int = 0,
    include_synthetic: bool = False,
) -> List[Dict[str, Any]]:
    """Returns immutable financial ledger transaction audit trail."""
    if not SessionLocal or not LedgerEntry or not BankrollService or not Bet or not Event:
        return []
    with SessionLocal() as session:
        account = BankrollService.get_or_create_account(session)
        q = session.query(LedgerEntry).filter(LedgerEntry.account_id == account.id)
        if not include_synthetic:
            q = (
                q.join(Bet, LedgerEntry.bet_id == Bet.id)
                .join(Event, Bet.event_id == Event.id)
                .filter(_combat_event_ids(Event.source_event_id))
            )
        entries = (
            q.order_by(LedgerEntry.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [
            {
                "id": str(e.id),
                "bet_id": str(e.bet_id) if e.bet_id else None,
                "entry_type": e.entry_type,
                "amount": float(e.amount),
                "balance_after": float(e.balance_after),
                "description": e.description,
                "created_at": e.created_at.isoformat(),
            }
            for e in entries
        ]


@app.get("/api/bets")
@app.get("/api/bankroll/bets")
def get_bets(
    status: Optional[str] = None,
    account_id: Optional[str] = None,
    include_synthetic: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Returns paper bets with optional status and account filtering (PENDING, WON, LOST, VOID)."""
    if not SessionLocal or not Bet or not Event or not VirtualAccount or not BankrollService:
        return []
    with SessionLocal() as session:
        q = session.query(Bet)
        if not include_synthetic:
            account = BankrollService.get_or_create_account(session)
            q = (
                q.join(Event, Bet.event_id == Event.id)
                .filter(Bet.account_id == account.id)
                .filter(_combat_event_ids(Event.source_event_id))
            )
        if account_id:
            try:
                acc_uuid = uuid.UUID(account_id)
                q = q.filter(Bet.account_id == acc_uuid)
            except ValueError:
                pass
        if status:
            q = q.filter(Bet.status == status.upper())
        bets = q.order_by(Bet.placed_at.desc()).offset(offset).limit(limit).all()
        event_ids = {b.event_id for b in bets}
        names = {}
        placed_models = {}
        if event_ids and Event is not None:
            for event in session.query(Event).filter(Event.id.in_(event_ids)).all():
                names[event.id] = (event.participant_a_name, event.participant_b_name)
        for bet in bets:
            if bet.proposal_id and BetProposal is not None:
                proposal = session.get(BetProposal, bet.proposal_id)
                if proposal is not None and proposal.ml_prediction_id and MLPrediction is not None:
                    prediction = session.get(MLPrediction, proposal.ml_prediction_id)
                    if prediction is not None:
                        detail = prediction.outcomes_detail or {}
                        placed_models[bet.id] = (
                            detail.get("model_name"),
                            prediction.model_version,
                        )
        return [
            {
                "id": str(b.id),
                "event_id": str(b.event_id),
                "proposal_id": str(b.proposal_id) if b.proposal_id else None,
                "sport_code": b.sport_code,
                "market": b.market,
                "outcome": b.outcome,
                "participant_a_name": (names.get(b.event_id) or (None, None))[0],
                "participant_b_name": (names.get(b.event_id) or (None, None))[1],
                "model_name": (placed_models.get(b.id) or (None, None))[0],
                "model_version": (placed_models.get(b.id) or (None, None))[1],
                "odds": float(b.odds),
                "stake": float(b.stake),
                "potential_payout": float(b.potential_payout),
                "potential_win": round(float(b.potential_payout) - float(b.stake), 2),
                "max_loss": float(b.stake),
                "status": b.status,
                "placed_at": b.placed_at.isoformat(),
            }
            for b in bets
        ]


@app.post("/api/execution/sync")
def sync_confirmed_bets(limit: int = 10) -> Dict[str, Any]:
    """Send confirmed ACTIVE tennis predictions to bet-manager. The ledger is not written here."""
    if not SessionLocal:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from bankroll.execution_bridge import execute_confirmed

    with SessionLocal() as session:
        report = execute_confirmed(session, limit=limit)
        session.commit()
        return {"status": "success", **report}


@app.get("/api/bets/{bet_id}")
def get_bet_detail(bet_id: str) -> Dict[str, Any]:
    """Returns single bet details with associated proposal, settlement, and audit trail."""
    if not SessionLocal or not Bet:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        b_uuid = uuid.UUID(bet_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid bet UUID")

    with SessionLocal() as session:
        b = session.query(Bet).filter(Bet.id == b_uuid).first()
        if not b:
            raise HTTPException(status_code=404, detail="Bet not found")

        settlement_dict = None
        if b.settlement:
            settlement_dict = {
                "id": str(b.settlement.id),
                "status": b.settlement.status,
                "payout": float(b.settlement.payout),
                "net_profit": float(b.settlement.net_profit),
                "settlement_reason": b.settlement.settlement_reason,
                "settled_at": b.settlement.settled_at.isoformat(),
            }

        validation = None
        if b.proposal_id and BetValidationResult:
            row = (
                session.query(BetValidationResult)
                .filter(BetValidationResult.proposal_id == b.proposal_id)
                .order_by(BetValidationResult.created_at.desc())
                .first()
            )
            if row is not None:
                validation = {
                    "is_accepted": row.is_accepted,
                    "rejection_code": row.rejection_code,
                    "checks_passed": row.checks_passed,
                    "checks_failed": row.checks_failed,
                    "details": getattr(row, "details", None) or {},
                }
        stake = float(b.stake)
        payout = float(b.potential_payout)
        return {
            "id": str(b.id),
            "account_id": str(b.account_id),
            "event_id": str(b.event_id),
            "proposal_id": str(b.proposal_id) if b.proposal_id else None,
            "sport_code": b.sport_code,
            "market": b.market,
            "outcome": b.outcome,
            "odds": float(b.odds),
            "stake": stake,
            "potential_payout": payout,
            "potential_win": round(payout - stake, 2),
            "max_loss": stake,
            "status": b.status,
            "placed_at": b.placed_at.isoformat(),
            "settlement": settlement_dict,
            "validation": validation,
        }


@app.get("/api/proposals")
def get_bet_proposals(event_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Returns evaluated bet proposals with validation checks."""
    if not SessionLocal or not BetProposal:
        return []

    with SessionLocal() as session:
        q = session.query(BetProposal)
        if event_id:
            try:
                q = q.filter(BetProposal.event_id == uuid.UUID(event_id))
            except ValueError:
                return []

        proposals = q.order_by(BetProposal.created_at.desc()).limit(limit).all()
        return [
            {
                "id": str(p.id),
                "event_id": str(p.event_id),
                "sport_code": p.sport_code,
                "market": p.market,
                "outcome": p.outcome,
                "bookmaker_odds": float(p.bookmaker_odds),
                "fair_odds": float(p.fair_odds),
                "model_probability": float(p.model_probability),
                "edge": float(p.edge),
                "suggested_stake": float(p.suggested_stake),
                "ml_prediction_id": str(p.ml_prediction_id) if p.ml_prediction_id else None,
                "llm_decision_id": str(p.llm_decision_id) if p.llm_decision_id else None,
                "created_at": p.created_at.isoformat(),
            }
            for p in proposals
        ]


# --------------------------------------------------------------------------
# 7. Portfolio Performance & Drawdown Metrics
# --------------------------------------------------------------------------
@app.get("/api/performance")
def get_portfolio_performance(include_synthetic: bool = False) -> Dict[str, Any]:
    """Calculates overall betting performance: PnL, ROI, Win Rate, and Max Drawdown."""
    empty = {
        "total_bets": 0, "won_bets": 0, "lost_bets": 0, "void_bets": 0, "pending_bets": 0,
        "win_rate": 0.0, "total_turnover": 0.0, "net_pnl": 0.0, "roi": 0.0, "max_drawdown": 0.0,
    }
    if not SessionLocal or not Bet or not VirtualAccount or not BankrollService or not Event or not BetSettlement:
        return empty

    with SessionLocal() as session:
        account = BankrollService.get_or_create_account(session)
        q = session.query(Bet).filter(Bet.account_id == account.id)
        if not include_synthetic:
            q = q.join(Event, Bet.event_id == Event.id).filter(_combat_event_ids(Event.source_event_id))
        bets = q.all()
        total_bets = len(bets)
        won_bets = sum(1 for b in bets if b.status == "WON")
        lost_bets = sum(1 for b in bets if b.status == "LOST")
        void_bets = sum(1 for b in bets if b.status == "VOID")
        pending_bets = sum(1 for b in bets if b.status == "PENDING")

        decided_bets = won_bets + lost_bets
        win_rate = round(won_bets / decided_bets, 4) if decided_bets > 0 else 0.0
        total_turnover = sum(float(b.stake) for b in bets)

        bet_ids = [b.id for b in bets]
        settlements = (
            session.query(BetSettlement).filter(BetSettlement.bet_id.in_(bet_ids)).all()
            if bet_ids else []
        )
        net_pnl = round(sum(float(s.net_profit) for s in settlements), 2)
        settled_turnover = sum(float(b.stake) for b in bets if b.status in ("WON", "LOST", "VOID"))
        roi = round((net_pnl / settled_turnover * 100.0), 2) if settled_turnover > 0 else 0.0

        max_dd = 0.0
        if bet_ids and LedgerEntry:
            entries = (
                session.query(LedgerEntry)
                .filter(LedgerEntry.account_id == account.id, LedgerEntry.bet_id.in_(bet_ids))
                .order_by(LedgerEntry.created_at.asc())
                .all()
            )
            peak = float(account.initial_balance)
            for e in entries:
                bal = float(e.balance_after)
                if bal > peak:
                    peak = bal
                dd = (peak - bal) / peak if peak > 0 else 0.0
                if dd > max_dd:
                    max_dd = dd

        return {
            "total_bets": total_bets,
            "won_bets": won_bets,
            "lost_bets": lost_bets,
            "void_bets": void_bets,
            "pending_bets": pending_bets,
            "win_rate": win_rate,
            "total_turnover": round(total_turnover, 2),
            "net_pnl": net_pnl,
            "roi": roi,
            "max_drawdown": round(max_dd, 4),
            "currency": "RUB",
        }


# --------------------------------------------------------------------------
# 8. ML Models & Training Runs Registry
# --------------------------------------------------------------------------
def _journal_market_family(market: str) -> str:
    """Match and set lines are scored separately. Any other market stays in the total only."""
    text = str(market or "")
    if text == "match_winner" or text.startswith("match_winner:"):
        return "match"
    if text == "set_winner" or text.startswith("set_winner:"):
        return "set"
    return "other"


@app.get("/api/models/live-record")
def get_model_live_record() -> Dict[str, Any]:
    """Win rate of ACTIVE and SHADOW models. Match, set, and the total are separate. Voids are excluded."""
    if not SessionLocal or not ModelVersion or not ModelBetJournal:
        return {"models": []}

    with SessionLocal() as session:
        models = (
            session.query(ModelVersion)
            .filter(ModelVersion.status.in_(["ACTIVE", "SHADOW"]))
            .order_by(
                case(
                    (ModelVersion.status == "ACTIVE", 0),
                    (ModelVersion.status == "SHADOW", 1),
                    else_=2,
                ),
                ModelVersion.sport_code.asc(),
                ModelVersion.created_at.desc(),
            )
            .all()
        )
        stats: Dict[tuple[str, str], Dict[str, int]] = {}
        rows = (
            session.query(
                ModelBetJournal.model_name,
                ModelBetJournal.model_version,
                ModelBetJournal.market,
                ModelBetJournal.result_status,
                func.count(ModelBetJournal.id),
            )
            .filter(ModelBetJournal.result_status.in_(["won", "lost"]))
            .group_by(
                ModelBetJournal.model_name,
                ModelBetJournal.model_version,
                ModelBetJournal.market,
                ModelBetJournal.result_status,
            )
            .all()
        )
        for model_name, version, market, status, count in rows:
            bucket = stats.setdefault(
                (model_name, version),
                {"settled": 0, "wins": 0, "match_settled": 0, "match_wins": 0, "set_settled": 0, "set_wins": 0},
            )
            amount = int(count)
            bucket["settled"] += amount
            won = status == "won"
            if won:
                bucket["wins"] += amount
            family = _journal_market_family(market)
            if family == "match":
                bucket["match_settled"] += amount
                if won:
                    bucket["match_wins"] += amount
            elif family == "set":
                bucket["set_settled"] += amount
                if won:
                    bucket["set_wins"] += amount

        empty = {"settled": 0, "wins": 0, "match_settled": 0, "match_wins": 0, "set_settled": 0, "set_wins": 0}
        payload = []
        for model in models:
            bucket = stats.get((model.model_name, model.version_tag), empty)
            settled = bucket["settled"]
            wins = bucket["wins"]
            payload.append(
                {
                    "id": str(model.id),
                    "model_name": model.model_name,
                    "display_name": (model.hyperparameters or {}).get("display_label") or model.model_name,
                    "is_composite": (model.hyperparameters or {}).get("kind") == "composite",
                    "components": (model.hyperparameters or {}).get("components") or [],
                    "version_tag": model.version_tag,
                    "sport_code": model.sport_code,
                    "status": model.status,
                    "settled": settled,
                    "wins": wins,
                    "win_rate": round(wins / settled, 4) if settled else None,
                    "match_settled": bucket["match_settled"],
                    "match_wins": bucket["match_wins"],
                    "match_win_rate": round(bucket["match_wins"] / bucket["match_settled"], 4) if bucket["match_settled"] else None,
                    "set_settled": bucket["set_settled"],
                    "set_wins": bucket["set_wins"],
                    "set_win_rate": round(bucket["set_wins"] / bucket["set_settled"], 4) if bucket["set_settled"] else None,
                }
            )
        return {"models": payload}


def _journal_period_stats(rows: list) -> Dict[str, Any]:
    """Win rates and averages for one time window. Voids stay out of the win rate."""
    settled = wins = losses = voids = pending = 0
    match_settled = match_wins = set_settled = set_wins = 0
    probability = odds = edge = expected = 0.0
    counted = 0
    curve: list[Dict[str, int]] = []
    bets = won = 0
    for market, status, probability_value, odds_value, edge_value, expected_value in rows:
        counted += 1
        probability += float(probability_value or 0)
        odds += float(odds_value or 0)
        edge += float(edge_value or 0)
        expected += float(expected_value or 0)
        if status == "pending":
            pending += 1
            continue
        if status == "void":
            voids += 1
            continue
        if status not in ("won", "lost"):
            continue
        settled += 1
        bets += 1
        is_win = status == "won"
        if is_win:
            wins += 1
            won += 1
        else:
            losses += 1
        family = _journal_market_family(str(market or ""))
        if family == "match":
            match_settled += 1
            if is_win:
                match_wins += 1
        elif family == "set":
            set_settled += 1
            if is_win:
                set_wins += 1
        curve.append({"bets": bets, "wins": won})
    return {
        "committed": counted,
        "settled": settled,
        "wins": wins,
        "losses": losses,
        "voids": voids,
        "pending": pending,
        "win_rate": round(wins / settled, 4) if settled else None,
        "match_settled": match_settled,
        "match_wins": match_wins,
        "match_win_rate": round(match_wins / match_settled, 4) if match_settled else None,
        "set_settled": set_settled,
        "set_wins": set_wins,
        "set_win_rate": round(set_wins / set_settled, 4) if set_settled else None,
        "avg_probability": round(probability / counted, 4) if counted else None,
        "avg_odds": round(odds / counted, 4) if counted else None,
        "avg_edge": round(edge / counted, 4) if counted else None,
        "avg_expected_value": round(expected / counted, 4) if counted else None,
        "curve": _thin_curve(curve),
    }


def _thin_curve(points: list[Dict[str, int]], limit: int = 240) -> list[Dict[str, int]]:
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    thinned = [points[int(index * step)] for index in range(limit - 1)]
    thinned.append(points[-1])
    return thinned


@app.get("/api/models/{model_id}/journal-stats")
def get_model_journal_stats(model_id: str) -> Dict[str, Any]:
    """Live journal of one registered model. A composite checkpoint is one model, not its parts."""
    if not SessionLocal or not ModelVersion or not ModelBetJournal:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        parsed = uuid.UUID(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="model_id must be a UUID") from exc
    now = datetime.now(timezone.utc)
    windows = {
        "all": None,
        "week": now - timedelta(days=7),
        "day": now - timedelta(days=1),
    }
    with SessionLocal() as session:
        model = session.get(ModelVersion, parsed)
        if model is None:
            raise HTTPException(status_code=404, detail="Model not found")
        stored = (
            session.query(
                ModelBetJournal.market,
                ModelBetJournal.result_status,
                ModelBetJournal.decided_at,
                ModelBetJournal.model_probability,
                ModelBetJournal.bookmaker_odds,
                ModelBetJournal.edge,
                ModelBetJournal.expected_value,
            )
            .filter(
                ModelBetJournal.model_name == model.model_name,
                ModelBetJournal.model_version == model.version_tag,
            )
            .order_by(ModelBetJournal.decided_at.asc(), ModelBetJournal.id.asc())
            .all()
        )
        periods = {}
        for name, start in windows.items():
            selected = []
            for market, status, decided_at, probability, odds, edge, expected in stored:
                moment = decided_at
                if moment is not None and moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                if start is not None and (moment is None or moment < start):
                    continue
                selected.append((market, status, probability, odds, edge, expected))
            periods[name] = _journal_period_stats(selected)
        return {
            "id": str(model.id),
            "model_name": model.model_name,
            "version_tag": model.version_tag,
            "is_composite": (model.hyperparameters or {}).get("kind") == "composite",
            "periods": periods,
        }


@app.get("/api/models")
def get_model_versions() -> List[Dict[str, Any]]:
    """Returns registered ML model versions and performance checkpoints."""
    if not SessionLocal or not ModelVersion:
        return []
    with SessionLocal() as session:
        models = (
            session.query(ModelVersion)
            .order_by(
                case(
                    (ModelVersion.status == "ACTIVE", 0),
                    (ModelVersion.status == "SHADOW", 1),
                    else_=2,
                ),
                ModelVersion.created_at.desc(),
            )
            .all()
        )
        return [
            {
                "id": str(m.id),
                "model_name": m.model_name,
                "version_tag": m.version_tag,
                "sport_code": m.sport_code,
                "market": m.market,
                "status": m.status,
                "metrics": m.metrics,
                "hyperparameters": m.hyperparameters,
                "display_name": (m.hyperparameters or {}).get("display_label") or m.model_name,
                "is_composite": (m.hyperparameters or {}).get("kind") == "composite",
                "created_at": m.created_at.isoformat(),
            }
            for m in models
        ]


class ShadowRegisterRequest(BaseModel):
    version_tag: str
    sport_code: str = "tennis"


class ShadowPromoteRequest(BaseModel):
    shadow_version_tag: str
    sport_code: str = "tennis"
    force: bool = False
    min_shadow_period_days: Optional[float] = None
    min_shadow_evaluations: Optional[int] = None
    max_acceptable_drift_psi: Optional[float] = None
    min_shadow_roi: Optional[float] = None
    max_acceptable_brier_delta: Optional[float] = None


@app.get("/api/models/shadow")
def get_shadow_models_status(sport_code: str = "tennis") -> Dict[str, Any]:
    """Returns active and shadow models with observation status and counts."""
    if not SessionLocal or not ModelVersion:
        return {"status": "unavailable", "detail": "Database connection not available"}

    with SessionLocal() as session:
        active_model = (
            session.query(ModelVersion)
            .filter(ModelVersion.sport_code == sport_code, ModelVersion.status == "ACTIVE")
            .order_by(ModelVersion.created_at.desc())
            .first()
        )
        shadow_model = (
            session.query(ModelVersion)
            .filter(ModelVersion.sport_code == sport_code, ModelVersion.status == "SHADOW")
            .order_by(ModelVersion.created_at.desc())
            .first()
        )

        active_count = 0
        shadow_count = 0
        if MLPrediction:
            active_count = session.query(func.count(MLPrediction.id)).filter(
                MLPrediction.is_shadow.is_(False)
            ).scalar() or 0
            shadow_count = session.query(func.count(MLPrediction.id)).filter(
                MLPrediction.is_shadow.is_(True)
            ).scalar() or 0

        shadow_period_days = 0.0
        if shadow_model:
            now = datetime.now(timezone.utc)
            created = shadow_model.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            shadow_period_days = round(max(0.0, (now - created).total_seconds() / 86400.0), 2)

        return {
            "sport_code": sport_code,
            "has_shadow_model": shadow_model is not None,
            "active_model": {
                "id": str(active_model.id),
                "version_tag": active_model.version_tag,
                "model_name": active_model.model_name,
                "metrics": active_model.metrics,
                "created_at": active_model.created_at.isoformat(),
            } if active_model else None,
            "shadow_model": {
                "id": str(shadow_model.id),
                "version_tag": shadow_model.version_tag,
                "model_name": shadow_model.model_name,
                "display_name": (shadow_model.hyperparameters or {}).get("display_label") or shadow_model.model_name,
                "is_composite": (shadow_model.hyperparameters or {}).get("kind") == "composite",
                "components": (shadow_model.hyperparameters or {}).get("components") or [],
                "metrics": shadow_model.metrics,
                "created_at": shadow_model.created_at.isoformat(),
                "observation_days": shadow_period_days,
            } if shadow_model else None,
            "metrics": {
                "active_predictions_count": active_count,
                "shadow_predictions_count": shadow_count,
                "shadow_observation_days": shadow_period_days,
            },
        }


@app.get("/api/models/shadow/report")
def get_shadow_evaluation_report(
    sport_code: str = "tennis",
    min_shadow_period_days: float = 7.0,
    min_shadow_evaluations: int = 100,
    max_acceptable_drift_psi: float = 0.25,
    min_shadow_roi: float = 0.0,
    max_acceptable_brier_delta: float = 0.02,
) -> Dict[str, Any]:
    """Generates comprehensive Shadow Evaluation Report comparing active vs shadow models."""
    if not SessionLocal or not ModelVersion or not ShadowEngine:
        raise HTTPException(status_code=503, detail="Database or ShadowEngine not available")

    with SessionLocal() as session:
        active_model = (
            session.query(ModelVersion)
            .filter(ModelVersion.sport_code == sport_code, ModelVersion.status == "ACTIVE")
            .order_by(ModelVersion.created_at.desc())
            .first()
        )
        shadow_model = (
            session.query(ModelVersion)
            .filter(ModelVersion.sport_code == sport_code, ModelVersion.status == "SHADOW")
            .order_by(ModelVersion.created_at.desc())
            .first()
        )

        active_tag = active_model.version_tag if active_model else "active-baseline"
        shadow_tag = shadow_model.version_tag if shadow_model else "shadow-candidate"

        config = ShadowConfig(
            active_model_version=active_tag,
            shadow_model_version=shadow_tag,
            sport_code=sport_code,
            min_shadow_period_days=min_shadow_period_days,
            min_shadow_evaluations=min_shadow_evaluations,
            max_acceptable_drift_psi=max_acceptable_drift_psi,
            min_shadow_roi=min_shadow_roi,
            max_acceptable_brier_delta=max_acceptable_brier_delta,
        )

        engine = ShadowEngine(config=config)
        now = datetime.now(timezone.utc)
        start_time = shadow_model.created_at if shadow_model else (now - timedelta(days=1))
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)

        # Query pairs of predictions from DB if available
        if MLPrediction:
            active_preds = (
                session.query(MLPrediction)
                .filter(
                    MLPrediction.is_shadow.is_(False),
                    MLPrediction.model_version == active_tag,
                )
                .order_by(MLPrediction.created_at.desc())
                .limit(500)
                .all()
            )
            shadow_preds = (
                session.query(MLPrediction)
                .filter(
                    MLPrediction.is_shadow.is_(True),
                    MLPrediction.model_version == shadow_tag,
                )
                .order_by(MLPrediction.created_at.desc())
                .limit(500)
                .all()
            )

            act_by_event = {(str(p.event_id), p.market): p for p in active_preds}
            for s_pred in shadow_preds:
                evt_id_str = str(s_pred.event_id)
                a_pred = act_by_event.get((evt_id_str, s_pred.market))
                if a_pred and OutcomePrediction and MLPredictionContract:
                    def to_contract(db_p, is_shd: bool):
                        outcomes = []
                        for o in (db_p.probabilities.get("outcomes", []) if isinstance(db_p.probabilities, dict) else []):
                            prob = float(o.get("model_probability", 0.5))
                            odds = float(o.get("bookmaker_odds", 2.0))
                            edge = float(o.get("edge", prob * odds - 1.0))
                            fair = float(o.get("fair_odds", round(1.0 / max(0.001, prob), 4)))
                            ev = float(o.get("expected_value", round(prob * (odds - 1.0) - (1.0 - prob), 4)))
                            conf = float(o.get("confidence", 0.85))
                            outcomes.append(
                                OutcomePrediction(
                                    outcome=str(o.get("outcome", "player_a")),
                                    model_probability=prob,
                                    bookmaker_odds=odds,
                                    fair_odds=fair,
                                    edge=edge,
                                    expected_value=ev,
                                    confidence=conf,
                                )
                            )
                        if not outcomes:
                            p_a = float(db_p.probabilities.get("player_a", 0.5)) if isinstance(db_p.probabilities, dict) else 0.5
                            p_b = round(1.0 - p_a, 4)
                            outcomes = [
                                OutcomePrediction(
                                    outcome="player_a",
                                    model_probability=p_a,
                                    bookmaker_odds=2.0,
                                    fair_odds=round(1.0 / max(0.001, p_a), 4),
                                    edge=round(p_a * 2.0 - 1.0, 4),
                                    expected_value=round(p_a * 1.0 - (1.0 - p_a), 4),
                                    confidence=0.85,
                                ),
                                OutcomePrediction(
                                    outcome="player_b",
                                    model_probability=p_b,
                                    bookmaker_odds=2.0,
                                    fair_odds=round(1.0 / max(0.001, p_b), 4),
                                    edge=round(p_b * 2.0 - 1.0, 4),
                                    expected_value=round(p_b * 1.0 - (1.0 - p_b), 4),
                                    confidence=0.85,
                                ),
                            ]
                        best_o = outcomes[0].outcome if outcomes[0].model_probability >= outcomes[1].model_probability else outcomes[1].outcome
                        best_edge_val = max(o.edge for o in outcomes)
                        return MLPredictionContract(
                            prediction_id=str(db_p.id),
                            event_id=str(db_p.event_id),
                            model_version=db_p.model_version,
                            sport_code=sport_code,
                            market="match_winner",
                            outcomes=outcomes,
                            best_outcome=best_o,
                            has_positive_edge=(best_edge_val > 0.0),
                            is_shadow=is_shd,
                        )

                    c_act = to_contract(a_pred, False)
                    c_shd = to_contract(s_pred, True)
                    prob_diff = round(abs(c_act.outcomes[0].model_probability - c_shd.outcomes[0].model_probability), 4)
                    act_max_e = max(o.edge for o in c_act.outcomes)
                    shd_max_e = max(o.edge for o in c_shd.outcomes)
                    pair = ShadowPredictionPair(
                        event_id=evt_id_str,
                        timestamp=s_pred.created_at,
                        active_prediction=c_act,
                        shadow_prediction=c_shd,
                        prob_diff_outcome_a=prob_diff,
                        outcomes_match=(c_act.best_outcome == c_shd.best_outcome),
                        edge_diff=round(abs(act_max_e - shd_max_e), 4),
                    )
                    engine.pairs_history.append(pair)

            if Event:
                completed_events = (
                    session.query(Event)
                    .filter(Event.status == "finished")
                    .limit(500)
                    .all()
                )
                for ev in completed_events:
                    meta = getattr(ev, "metadata_json", {}) or {}
                    score = getattr(ev, "current_score", {}) or {}
                    winner = meta.get("winner") or score.get("winner") or "player_a"
                    engine.register_ground_truth(str(ev.id), winner)

        report = engine.generate_evaluation_report(
            shadow_start_time=start_time,
            shadow_end_time=now,
        )
        return report.model_dump()


@app.post("/api/models/shadow/register")
def register_shadow_model(req: ShadowRegisterRequest) -> Dict[str, Any]:
    """Transitions or registers a model version into SHADOW status."""
    if not SessionLocal or not ModelVersion:
        raise HTTPException(status_code=503, detail="Database not available")

    with SessionLocal() as session:
        candidate = (
            session.query(ModelVersion)
            .filter(ModelVersion.version_tag == req.version_tag)
            .first()
        )
        if not candidate:
            raise HTTPException(status_code=404, detail=f"Model version {req.version_tag} not found")

        # Retire any prior shadow models for this sport
        prior_shadows = (
            session.query(ModelVersion)
            .filter(ModelVersion.sport_code == req.sport_code, ModelVersion.status == "SHADOW")
            .all()
        )
        for p in prior_shadows:
            if p.id != candidate.id:
                p.status = "RETIRED"

        candidate.status = "SHADOW"
        candidate.sport_code = req.sport_code
        session.commit()

        return {
            "status": "success",
            "message": f"Model {req.version_tag} registered into SHADOW status",
            "version_tag": candidate.version_tag,
            "model_status": candidate.status,
            "created_at": candidate.created_at.isoformat(),
        }


@app.post("/api/models/shadow/promote")
def promote_shadow_model(req: ShadowPromoteRequest) -> Dict[str, Any]:
    """
    Promotes a SHADOW model to ACTIVE only if it satisfies all empirical promotion gates.
    Blocks promotion with 400 Bad Request if validation gates fail.
    """
    if not SessionLocal or not ModelVersion:
        raise HTTPException(status_code=503, detail="Database not available")

    with SessionLocal() as session:
        candidate = (
            session.query(ModelVersion)
            .filter(ModelVersion.version_tag == req.shadow_version_tag)
            .first()
        )
        if not candidate:
            raise HTTPException(status_code=404, detail=f"Candidate model {req.shadow_version_tag} not found")
        if str(candidate.version_tag or "").startswith("test_"):
            raise HTTPException(status_code=400, detail="A test version cannot be promoted")

        if candidate.status != "SHADOW" and not req.force:
            raise HTTPException(
                status_code=400,
                detail=f"Candidate model must be in SHADOW status to promote (current: {candidate.status})",
            )

        # Generate evaluation report to verify promotion criteria
        report_data = get_shadow_evaluation_report(
            sport_code=req.sport_code,
            min_shadow_period_days=req.min_shadow_period_days or 7.0,
            min_shadow_evaluations=req.min_shadow_evaluations or 100,
            max_acceptable_drift_psi=req.max_acceptable_drift_psi or 0.25,
            min_shadow_roi=req.min_shadow_roi or 0.0,
            max_acceptable_brier_delta=req.max_acceptable_brier_delta or 0.02,
        )

        meets_criteria = report_data.get("meets_promotion_criteria", False)
        rejection_reasons = report_data.get("promotion_rejection_reasons", [])

        if not meets_criteria and not req.force:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "PROMOTION_GATE_FAILED",
                    "message": "Candidate model failed mandatory shadow promotion criteria",
                    "rejection_reasons": rejection_reasons,
                    "report": report_data,
                },
            )

        # Retire the previous combat version of this line. The other line of the sport stays.
        active_models = (
            session.query(ModelVersion)
            .filter(
                ModelVersion.sport_code == req.sport_code,
                ModelVersion.model_name == candidate.model_name,
                ModelVersion.status == "ACTIVE",
            )
            .all()
        )
        for act in active_models:
            act.status = "RETIRED"

        candidate.status = "ACTIVE"
        session.commit()

        return {
            "status": "promoted",
            "message": f"Model {candidate.version_tag} successfully promoted to ACTIVE status",
            "promoted_version": candidate.version_tag,
            "sport_code": candidate.sport_code,
            "promotion_report": report_data,
        }


@app.post("/api/models/train")
def trigger_model_training(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Puts a real training job on the Redis stream. The neural service fits the model."""
    import redis
    from streams import STREAM_TRAINING_JOBS, StreamPublisher

    body = payload or {}
    sport_code = str(body.get("sport_code") or "tennis").lower()
    model_type = str(body.get("model_type") or "gb").lower()
    if model_type not in ("lr", "gb", "gbdt", "all", "epoch_lr", "epoch_gb", "epoch_mlp", "epoch_all"):
        raise HTTPException(status_code=400, detail="model_type must be lr, gb, gbdt, all, or an epoch line")
    bump = str(body.get("bump") or "patch")
    if bump not in ("patch", "minor", "major", "test"):
        raise HTTPException(status_code=400, detail="bump must be patch, minor, major, or test")
    hyperparameters = body.get("hyperparameters") if isinstance(body.get("hyperparameters"), dict) else {}
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    try:
        client = redis.Redis.from_url(redis_url, socket_timeout=2)
        client.ping()
        envelope = StreamPublisher(client, producer="backend").publish(
            STREAM_TRAINING_JOBS,
            {
                "sport_code": sport_code,
                "model_type": model_type,
                "hyperparameters": hyperparameters,
                "bump": bump,
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Training queue is unavailable: {exc}") from exc
    return {
        "status": "queued",
        "sport_code": sport_code,
        "model_type": model_type,
        "bump": bump,
        "message_id": envelope.message_id,
        "message": f"Training job for {sport_code} is queued for the neural service",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/training/runs")
def get_training_runs(limit: int = 50) -> List[Dict[str, Any]]:
    """Returns historical ML training runs with datasets and validation scores."""
    if not SessionLocal or not TrainingRun:
        return []
    with SessionLocal() as session:
        runs = session.query(TrainingRun).order_by(TrainingRun.started_at.desc()).limit(limit).all()
        return [
            {
                "id": str(r.id),
                "model_version_id": str(r.model_version_id) if r.model_version_id else None,
                "dataset_id": str(r.dataset_id) if r.dataset_id else None,
                "sport_code": r.sport_code,
                "status": r.status,
                "metrics_train": r.metrics_train,
                "metrics_val": r.metrics_val,
                "started_at": r.started_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in runs
        ]


@app.get("/api/experiments")
def get_experiment_results(limit: int = 50) -> List[Dict[str, Any]]:
    """Returns list of saved scientific experiments and backtest benchmarks."""
    if not SessionLocal or not ExperimentResult:
        return []
    with SessionLocal() as session:
        exps = session.query(ExperimentResult).order_by(ExperimentResult.created_at.desc()).limit(limit).all()
        return [
            {
                "id": str(e.id),
                "name": e.name,
                "model_version_id": str(e.model_version_id) if e.model_version_id else None,
                "strategy_config": e.strategy_config,
                "backtest_pnl": float(e.backtest_pnl),
                "backtest_roi": float(e.backtest_roi),
                "win_rate": float(e.win_rate),
                "max_drawdown": float(e.max_drawdown),
                "created_at": e.created_at.isoformat(),
            }
            for e in exps
        ]


@app.get("/api/experiments/comparison")
def get_latest_comparison_report() -> Dict[str, Any]:
    """Returns the latest scientific ML vs ML+LLM comparison report."""
    if not SessionLocal or not ExperimentResult:
        raise HTTPException(status_code=503, detail="Database unavailable")
    with SessionLocal() as session:
        exp = (
            session.query(ExperimentResult)
            .filter(ExperimentResult.name.like("comparison_%"))
            .order_by(ExperimentResult.created_at.desc())
            .first()
        )
        if not exp:
            raise HTTPException(status_code=404, detail="No comparative experiment found")
        comp = (exp.strategy_config or {}).get("comparison_report", {})
        return comp if comp else exp.strategy_config


@app.get("/api/experiments/{experiment_id}")
def get_experiment_detail(experiment_id: str) -> Dict[str, Any]:
    """Returns detailed result for a single scientific experiment."""
    if not SessionLocal or not ExperimentResult:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        e_uuid = uuid.UUID(experiment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid experiment UUID")

    with SessionLocal() as session:
        e = session.query(ExperimentResult).filter(ExperimentResult.id == e_uuid).first()
        if not e:
            raise HTTPException(status_code=404, detail="Experiment not found")
        return {
            "id": str(e.id),
            "name": e.name,
            "model_version_id": str(e.model_version_id) if e.model_version_id else None,
            "strategy_config": e.strategy_config,
            "backtest_pnl": float(e.backtest_pnl),
            "backtest_roi": float(e.backtest_roi),
            "win_rate": float(e.win_rate),
            "max_drawdown": float(e.max_drawdown),
            "created_at": e.created_at.isoformat(),
        }


@app.post("/api/experiments/run")
def trigger_experiment_run(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Triggers or records a scientific baseline experiment."""
    return {
        "status": "queued",
        "experiment_name": (payload or {}).get("name", "baseline_ml_only_tennis_h2h_2026"),
        "message": "Scientific baseline experiment submitted to neural experiment runner",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _backtest_hash(body: Dict[str, Any]) -> str:
    import hashlib

    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: strip(item) for key, item in value.items() if key not in ("timestamp", "schema_version")}
        if isinstance(value, list):
            return [strip(item) for item in value]
        return value

    raw = json.dumps(strip(body), sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _backtest_payload(run, results, include_bets: bool) -> Dict[str, Any]:
    rows = []
    for result in results:
        summary = dict(result.summary or {})
        if not include_bets:
            summary.pop("bet_records", None)
        summary["model_version_id"] = str(result.model_version_id) if result.model_version_id else summary.get("model_version_id")
        rows.append(summary)
    return {
        "id": str(run.id),
        "status": run.status,
        "sport_code": run.sport_code,
        "scope": run.scope,
        "request": run.request_json,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "results": rows,
    }


@app.get("/api/sports/tennis/policy")
def get_tennis_policy() -> Dict[str, Any]:
    """Numbers from sports/tennis/tennis.cfg. The form may change them for one replay."""
    from tennis_adapter.policy import load_tennis_policy

    return load_tennis_policy().as_dict()


@app.get("/api/tennis/odds-free/strategy-replay")
def get_odds_free_tennis_strategy_replay(
    confirm_positive_ev: bool = False,
) -> Dict[str, Any]:
    """Read-only EV replay for all live and the next six hours of prematch tennis."""
    from tennis_adapter.odds_free_replay import (
        PREMATCH_HORIZON_HOURS, odds_free_event_filters, replay_odds_free_forecasts,
    )
    from tennis_adapter.policy import load_tennis_policy

    if not SessionLocal or not Event or not TennisForecastSnapshot or not OddsSnapshot:
        raise HTTPException(status_code=503, detail="Database unavailable")
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=6)
    with SessionLocal() as session:
        events = session.query(Event).filter(
            *odds_free_event_filters(Event, now),
        ).order_by(Event.is_live.desc(), Event.scheduled_start_at.asc()).all()
        event_ids = [event.id for event in events]
        if not event_ids:
            report = replay_odds_free_forecasts([], [], policy=load_tennis_policy(),
                                                confirm_positive_ev=confirm_positive_ev)
            return {**report, "generated_at": now.isoformat(), "eligible_event_count": 0,
                    "live_event_count": 0, "prematch_event_count": 0,
                    "prematch_horizon_hours": PREMATCH_HORIZON_HOURS}
        forecast_rows = session.query(TennisForecastSnapshot).filter(
            TennisForecastSnapshot.event_id.in_(event_ids),
            TennisForecastSnapshot.observed_at >= since,
        ).order_by(TennisForecastSnapshot.event_id, TennisForecastSnapshot.model_name,
                   TennisForecastSnapshot.model_version, TennisForecastSnapshot.observed_at,
                   TennisForecastSnapshot.id).all()
        forecast_event_ids = list({row.event_id for row in forecast_rows})
        odds_rows = session.query(OddsSnapshot).filter(
            OddsSnapshot.event_id.in_(forecast_event_ids), OddsSnapshot.observed_at >= since,
        ).order_by(OddsSnapshot.event_id, OddsSnapshot.observed_at, OddsSnapshot.id).all() if forecast_event_ids else []
        forecasts_by_event: dict[str, list[dict]] = {}
        for snapshot in forecast_rows:
            row = dict(snapshot.forecast_json or {})
            row.update({
                "forecast_id": str(snapshot.id), "event_id": str(snapshot.event_id),
                "model_name": snapshot.model_name, "model_version": snapshot.model_version,
                "observed_at": snapshot.observed_at.isoformat(),
            })
            forecasts_by_event.setdefault(str(snapshot.event_id), []).append(row)
        odds_by_event: dict[str, list[dict]] = {}
        for snapshot in odds_rows:
            odds_by_event.setdefault(str(snapshot.event_id), []).append({
                "event_id": str(snapshot.event_id), "market_type": snapshot.market_type,
                "line_value": snapshot.line_value,
                "outcome": snapshot.outcome, "odds": float(snapshot.odds),
                "status": snapshot.status, "observed_at": snapshot.observed_at.isoformat(),
            })
        flattened_forecasts = [row for rows in forecasts_by_event.values() for row in rows]
        flattened_odds = [row for rows in odds_by_event.values() for row in rows]
        report = replay_odds_free_forecasts(
            flattened_forecasts, flattened_odds, policy=load_tennis_policy(),
            confirm_positive_ev=confirm_positive_ev,
        )
        return {
            **report,
            "generated_at": now.isoformat(),
            "eligible_event_count": len(events),
            "live_event_count": sum(bool(event.is_live or event.status == "live") for event in events),
            "prematch_event_count": sum(not bool(event.is_live or event.status == "live") for event in events),
            "prematch_horizon_hours": PREMATCH_HORIZON_HOURS,
            "history_horizon_hours": 6,
        }


@app.post("/api/backtests/run")
def trigger_backtest_run(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Queue a historical replay of ACTIVE/SHADOW checkpoints. Production bets are not created."""
    import redis
    from contracts import BacktestRunRequest
    from streams import STREAM_BACKTEST_JOBS, StreamPublisher

    if not SessionLocal or not BacktestRun or not ModelVersion:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        request = BacktestRunRequest.model_validate(payload or {})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    body = request.model_dump(mode="json")
    sport = request.selector.sport_code.strip().lower()
    if sport != "tennis":
        raise HTTPException(status_code=400, detail="Historical replay is available for tennis only")
    body["selector"]["sport_code"] = sport
    digest = _backtest_hash({"selector": body["selector"], "config": body["config"]})
    with SessionLocal() as session:
        if request.selector.scope == "model":
            try:
                model_id = uuid.UUID(str(request.selector.model_version_id))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="model_version_id must be a UUID") from exc
            model = session.get(ModelVersion, model_id)
            if model is None or model.status not in ("ACTIVE", "SHADOW"):
                raise HTTPException(status_code=400, detail="Select an ACTIVE or SHADOW model")
            if str(model.version_tag or "").startswith("test_"):
                raise HTTPException(status_code=400, detail="A test tag cannot be replayed")
            if model.sport_code != sport:
                raise HTTPException(status_code=400, detail="Model sport does not match sport_code")
        if request.config.event_ids:
            if Event is None:
                raise HTTPException(status_code=503, detail="Database unavailable")
            import uuid as uuid_lib

            parsed = [uuid_lib.UUID(item) for item in request.config.event_ids]
            found = (
                session.query(Event)
                .filter(
                    Event.id.in_(parsed),
                    Event.sport_code == sport,
                    Event.status.in_(["finished", "completed"]),
                )
                .count()
            )
            if found != len(parsed):
                raise HTTPException(status_code=400, detail="event_ids must be finished matches of the selected sport")
        existing = (
            session.query(BacktestRun)
            .filter(BacktestRun.config_hash == digest, BacktestRun.status.in_(["queued", "running"]))
            .first()
        )
        if existing is not None:
            return _backtest_payload(existing, [], include_bets=False) | {"deduplicated": True}
        _trim_backtest_history(session, keep=24)
        run = BacktestRun(
            status="queued",
            sport_code=sport,
            scope=request.selector.scope,
            model_version_id=uuid.UUID(str(request.selector.model_version_id)) if request.selector.scope == "model" else None,
            request_json=body,
            config_hash=digest,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = str(run.id)
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    try:
        client = redis.Redis.from_url(redis_url, socket_timeout=2)
        client.ping()
        envelope = StreamPublisher(client, producer="backend").publish(
            STREAM_BACKTEST_JOBS,
            {"run_id": run_id},
            idempotency_key=f"backtest:{digest}:{run_id}",
        )
    except Exception as exc:
        with SessionLocal() as session:
            failed = session.get(BacktestRun, uuid.UUID(run_id))
            if failed is not None:
                failed.status = "failed"
                failed.error = f"Queue unavailable: {exc}"[:2000]
                session.commit()
        raise HTTPException(status_code=503, detail=f"Backtest queue is unavailable: {exc}") from exc
    return {
        "id": run_id,
        "status": "queued",
        "sport_code": sport,
        "scope": request.selector.scope,
        "message_id": envelope.message_id,
        "deduplicated": False,
    }


@app.post("/api/backtests/strategy-search")
def trigger_strategy_search(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Queue a tennis gate search. It replays one SHADOW checkpoint on a fixed window."""
    import redis
    from contracts import StrategySearchRequest
    from streams import STREAM_BACKTEST_JOBS, StreamPublisher

    if not SessionLocal or not BacktestRun or not ModelVersion:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        request = StrategySearchRequest.model_validate(payload or {})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if request.sport_code.strip().lower() != "tennis":
        raise HTTPException(status_code=400, detail="Strategy search is available for tennis only")
    body = request.model_dump(mode="json")
    body["kind"] = "strategy_search"
    digest = _backtest_hash(body)
    with SessionLocal() as session:
        model = session.get(ModelVersion, uuid.UUID(request.model_version_id))
        if model is None or model.status not in ("ACTIVE", "SHADOW") or str(model.version_tag or "").startswith("test_"):
            raise HTTPException(status_code=400, detail="Select an ACTIVE or SHADOW model")
        run = BacktestRun(
            status="queued",
            sport_code="tennis",
            scope="model",
            model_version_id=model.id,
            request_json=body,
            config_hash=digest,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = str(run.id)
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    try:
        client = redis.Redis.from_url(redis_url, socket_timeout=2)
        client.ping()
        envelope = StreamPublisher(client, producer="backend").publish(
            STREAM_BACKTEST_JOBS,
            {"run_id": run_id},
            idempotency_key=f"strategy:{digest}:{run_id}",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Backtest queue is unavailable: {exc}") from exc
    return {"id": run_id, "status": "queued", "message_id": envelope.message_id}


@app.get("/api/backtests/runs")
def list_backtest_runs(
    sport_code: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> List[Dict[str, Any]]:
    """Recent historical replays without the full bet list."""
    if not SessionLocal or not BacktestRun:
        return []
    with SessionLocal() as session:
        _trim_backtest_history(session, keep=25)
        session.commit()
        query = session.query(BacktestRun)
        if sport_code:
            query = query.filter(BacktestRun.sport_code == sport_code.lower())
        runs = query.order_by(BacktestRun.created_at.desc()).limit(limit).all()
        payloads = []
        for run in runs:
            results = []
            if BacktestRunResult is not None:
                results = session.query(BacktestRunResult).filter(BacktestRunResult.run_id == run.id).all()
            payloads.append(_backtest_payload(run, results, include_bets=False))
        return payloads


@app.get("/api/backtests/runs/{run_id}")
def get_backtest_run(run_id: str) -> Dict[str, Any]:
    """One historical replay, including paper bets and the copied JSON report."""
    if not SessionLocal or not BacktestRun or not BacktestRunResult:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid backtest run id") from exc
    with SessionLocal() as session:
        run = session.get(BacktestRun, run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail="Backtest run not found")
        results = session.query(BacktestRunResult).filter(BacktestRunResult.run_id == run.id).all()
        return _backtest_payload(run, results, include_bets=True)


def _trim_backtest_history(session, keep: int) -> None:
    """Keep at most `keep` replays. A queued or running replay is not removed."""
    if not BacktestRun:
        return
    total = session.query(BacktestRun).count()
    extra = total - keep
    if extra <= 0:
        return
    doomed = (
        session.query(BacktestRun)
        .filter(BacktestRun.status.notin_(["queued", "running"]))
        .order_by(BacktestRun.created_at.asc(), BacktestRun.id.asc())
        .limit(extra)
        .all()
    )
    for old in doomed:
        if BacktestRunResult is not None:
            session.query(BacktestRunResult).filter(BacktestRunResult.run_id == old.id).delete()
        session.delete(old)


@app.delete("/api/backtests/runs/{run_id}")
def delete_backtest_run(run_id: str) -> Dict[str, Any]:
    """Remove a historical replay and its paper results. Production bets stay untouched."""
    if not SessionLocal or not BacktestRun:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid backtest run id") from exc
    with SessionLocal() as session:
        run = session.get(BacktestRun, run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail="Backtest run not found")
        status = run.status
        if BacktestRunResult is not None:
            session.query(BacktestRunResult).filter(BacktestRunResult.run_id == run.id).delete()
        session.delete(run)
        session.commit()
    return {"id": run_id, "deleted": True, "status": status}


# --------------------------------------------------------------------------
# 9. Simulation Reset & Bankroll Reconciliation
# --------------------------------------------------------------------------
@app.post("/api/simulation/reset")
def reset_simulation(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Resets virtual bankroll to initial deposit balance (1,000 RUB).
    Creates an explicit RESET transaction in the immutable ledger.
    """
    if not SessionLocal or not VirtualAccount or not LedgerEntry or not BankrollService:
        return {"error": "Bankroll service unavailable"}

    with SessionLocal() as session:
        account = BankrollService.get_or_create_account(session)
        init_bal = float(account.initial_balance)
        curr_bal = float(account.balance)
        diff = init_bal - curr_bal

        account.balance = init_bal
        account.locked_exposure = 0.0

        entry = LedgerEntry(
            id=uuid.uuid4(),
            account_id=account.id,
            entry_type="SIMULATION_RESET",
            amount=diff,
            balance_after=init_bal,
            description="Virtual simulation reset to initial balance",
        )
        session.add(entry)
        session.commit()

        return {
            "status": "success",
            "account_id": str(account.id),
            "new_balance": init_bal,
            "locked_exposure": 0.0,
            "message": "Virtual bankroll successfully reset to initial state",
        }


@app.post("/api/bankroll/reconcile")
def reconcile_bankroll() -> Dict[str, Any]:
    """Runs on-demand reconciliation of bankroll from ledger journal."""
    if not ReconciliationEngine or not BankrollService or not SessionLocal:
        return {"error": "Reconciliation engine unavailable"}
    with SessionLocal() as session:
        account = BankrollService.get_or_create_account(session)
        report = ReconciliationEngine.reconcile_account(session, account.id)
        session.commit()
        return report.model_dump()


# --------------------------------------------------------------------------
# 10. Live Updates via Server-Sent Events (SSE) (Architecture Section 31)
# --------------------------------------------------------------------------
@app.get("/api/live/stream")
async def live_stream_updates(request: Request, max_heartbeats: Optional[int] = None):
    """
    Server-Sent Events (SSE) stream broadcasting live match state changes,
    odds shifts, and decision updates to the frontend dashboard.

    The response stays open. Heartbeats keep proxies and the browser EventSource
    from treating a short payload as a dropped connection. max_heartbeats is only
    for tests that must read a finite body.
    """
    async def event_generator():
        yield (
            "event: connected\n"
            f"data: {json.dumps({'message': 'Connected to Neurobet live stream', 'time': datetime.now(timezone.utc).isoformat()})}\n\n"
        )
        count = 0
        while max_heartbeats is None or count < max_heartbeats:
            if await request.is_disconnected():
                break
            count += 1
            data = {
                "heartbeat": count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "active",
            }
            yield f"event: heartbeat\ndata: {json.dumps(data)}\n\n"
            if max_heartbeats is not None and count >= max_heartbeats:
                break
            await asyncio.sleep(15)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------
# 11. Settlement, Decision Layer & Scheduler Endpoints (Phase 13, 16, 17)
# --------------------------------------------------------------------------
_settlement_sync_lock = threading.Lock()


@app.post("/api/settlement/sync")
def sync_results_and_settle(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Sync match results from official Fonbet results feed,
    complete finished events in DB, and settle pending bets with strict idempotency.
    """
    if not SessionLocal or not FonbetResultsParser or not EventResultMatcher or not BetSettlementEngine:
        return {"error": "Settlement engine unavailable"}

    if not _settlement_sync_lock.acquire(blocking=False):
        return {
            "status": "busy",
            "results_parsed": 0,
            "events_matched": 0,
            "events_finished": 0,
            "bets_settled": 0,
            "review_queue_count": 0,
        }

    raw_data = payload or {}
    results_list = []

    parse_results = getattr(FonbetResultsParser, "parse_all_sports", None)
    if parse_results is None:
        parse_results = FonbetResultsParser.parse_feed

    if "events" in raw_data and "sections" in raw_data:
        results_list = parse_results(raw_data)
    elif "events" in raw_data and "eventMiscs" in raw_data and hasattr(FonbetResultsParser, "parse_get_by_date"):
        results_list = FonbetResultsParser.parse_get_by_date(raw_data)
    else:
        import subprocess
        for url in [
            "https://clientsapi-lb51.bk6bba-resources.com/results/results.json",
            "https://clientsapi-lb52.bk6bba-resources.ru/results/results.json",
        ]:
            try:
                proc = subprocess.run(
                    ["curl", "-s", "--compressed", "--connect-timeout", "5", "--max-time", "15", url],
                    stdout=subprocess.PIPE,
                    timeout=20,
                )
                if proc.returncode == 0 and proc.stdout:
                    parsed_feed = json.loads(proc.stdout.decode("utf-8", errors="replace"))
                    if "events" in parsed_feed and "sections" in parsed_feed:
                        results_list = parse_results(parsed_feed)
                        break
            except Exception:
                pass

        # For matches that have been running or were scheduled > 6 hours ago,
        # check if they have finished using https://fon.bet/results (date-based results feed)
        from sqlalchemy import or_, select
        from datetime import datetime, timezone, timedelta
        now_dt = datetime.now(timezone.utc)
        cutoff = now_dt - timedelta(hours=6)
        dates_to_query = set()

        try:
            with SessionLocal() as session:
                # 1. PENDING bets on events scheduled/started/placed > 6h ago
                pending_query = (
                    select(Event.scheduled_start_at, Event.started_at, Bet.placed_at)
                    .join(Bet, Bet.event_id == Event.id)
                    .where(
                        Bet.status == "PENDING",
                        Event.source == "fonbet",
                        or_(
                            Event.scheduled_start_at <= cutoff,
                            Event.started_at <= cutoff,
                            Bet.placed_at <= cutoff,
                        ),
                    )
                )
                for sched, started, placed in session.execute(pending_query).all():
                    for dt in (sched, started, placed):
                        if dt:
                            dates_to_query.add(dt.astimezone(timezone.utc).strftime("%Y-%m-%d"))
                            # Also add Moscow date (UTC+3)
                            dates_to_query.add((dt + timedelta(hours=3)).strftime("%Y-%m-%d"))

                # 2. Events marked live/interrupted running > 6h ago
                running_query = (
                    select(Event.scheduled_start_at, Event.started_at)
                    .where(
                        Event.source == "fonbet",
                        Event.status.in_(["live", "interrupted"]),
                        or_(
                            Event.scheduled_start_at <= cutoff,
                            Event.started_at <= cutoff,
                        ),
                    )
                )
                for sched, started in session.execute(running_query).all():
                    for dt in (sched, started):
                        if dt:
                            dates_to_query.add(dt.astimezone(timezone.utc).strftime("%Y-%m-%d"))
                            dates_to_query.add((dt + timedelta(hours=3)).strftime("%Y-%m-%d"))

        except Exception as ex:
            logger.warning("Error querying overdue events for results check: %s", ex)

        # If there are overdue events or bets, also ensure yesterday and today are checked
        if dates_to_query:
            dates_to_query.add(now_dt.strftime("%Y-%m-%d"))
            dates_to_query.add((now_dt - timedelta(days=1)).strftime("%Y-%m-%d"))

            fetch_by_date = getattr(FonbetResultsParser, "fetch_results_by_date", None)
            parse_by_date = getattr(FonbetResultsParser, "parse_get_by_date", None)
            if fetch_by_date and parse_by_date:
                for date_str in sorted(dates_to_query, reverse=True):
                    try:
                        date_payload = fetch_by_date(date_str)
                        if date_payload and "events" in date_payload:
                            date_results = parse_by_date(date_payload)
                            logger.info(
                                "Retrieved %d results from Fonbet results for date %s",
                                len(date_results),
                                date_str,
                            )
                            results_list.extend(date_results)
                    except Exception as ex:
                        logger.warning("Failed to fetch/parse results for date %s: %s", date_str, ex)

    # Deduplicate results keeping strongest definitive result
    dedup: Dict[str, CanonicalMatchResult] = {}
    for res in results_list:
        eid = str(res.source_event_id)
        if eid not in dedup:
            dedup[eid] = res
        else:
            existing = dedup[eid]
            if not EventResultMatcher.result_is_definitive(existing) and EventResultMatcher.result_is_definitive(res):
                dedup[eid] = res
    results_list = list(dedup.values())


    events_matched = 0
    events_finished = 0
    bets_settled = 0
    try:
        with SessionLocal() as session:
            index = EventResultMatcher.build_index(session)
        for res in results_list:
            definitive = EventResultMatcher.result_is_definitive(res)
            if EventResultMatcher.find_indexed_event(index, res, allow_live=definitive) is None:
                continue
            with SessionLocal() as session:
                matched_evt = EventResultMatcher.match_and_complete_event(session, res, index=index)
                if matched_evt:
                    events_matched += 1
                    if definitive and matched_evt.status in ("finished", "cancelled"):
                        events_finished += 1
                        settlements = BetSettlementEngine.settle_event_bets(session, matched_evt, res)
                        bets_settled += len(settlements)
                session.commit()
        with SessionLocal() as session:
            review_queue = BetSettlementEngine.get_review_queue(session)
        return {
            "status": "success",
            "results_parsed": len(results_list),
            "events_matched": events_matched,
            "events_finished": events_finished,
            "bets_settled": bets_settled,
            "review_queue_count": len(review_queue),
        }
    finally:
        _settlement_sync_lock.release()


@app.get("/api/settlement/review-queue")
def get_settlement_review_queue() -> List[Dict[str, Any]]:
    """Returns matches marked SETTLEMENT_REVIEW_REQUIRED that cannot be settled automatically."""
    if not SessionLocal or not BetSettlementEngine:
        return []
    with SessionLocal() as session:
        return BetSettlementEngine.get_review_queue(session)


@app.get("/api/settlement/history")
def get_settlement_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Returns list of settled bets with payout, PnL, and official reasons."""
    if not SessionLocal or not BetSettlement:
        return []
    with SessionLocal() as session:
        settlements = (
            session.query(BetSettlement)
            .order_by(BetSettlement.settled_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": str(s.id),
                "bet_id": str(s.bet_id),
                "status": s.status,
                "payout": float(s.payout),
                "net_profit": float(s.net_profit),
                "settlement_reason": s.settlement_reason,
                "settled_at": s.settled_at.isoformat(),
            }
            for s in settlements
        ]


@app.post("/api/decisions/evaluate")
def evaluate_decision_pipeline(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluates betting options through CandidateSelector and CombinedDecisionPipeline.
    Persists qualitative LLM decisions to DB and routes approved candidates as BetProposals.
    """
    if not SessionLocal or not CandidateSelector or not CombinedDecisionPipeline:
        return {"error": "Decision pipeline unavailable"}

    raw_options = payload.get("options", [])
    raw_cfg = payload.get("config", {})
    config = DecisionPipelineConfig(**raw_cfg) if raw_cfg else DecisionPipelineConfig()

    candidates = []
    for opt in raw_options:
        prob = float(opt["model_probability"])
        odds = float(opt["odds"])
        cand = CandidateSelector.evaluate_candidate(
            event_id=str(opt["event_id"]),
            sport_code=opt.get("sport_code", "tennis"),
            participant_a=opt["participant_a"],
            participant_b=opt["participant_b"],
            tournament=opt.get("tournament"),
            market=opt.get("market", "match_winner"),
            selection=opt["selection"],
            odds=odds,
            model_probability=prob,
            confidence=float(opt.get("confidence", 0.6)),
            model_version=opt.get("model_version", "tennis_baseline_v1"),
            min_edge=config.min_edge,
            min_confidence=config.min_confidence,
        )
        candidates.append(cand)

    shortlisted = [c for c in candidates if c.is_shortlisted]
    results = []

    with SessionLocal() as session:
        pipeline = CombinedDecisionPipeline(config=config)
        for c in candidates:
            if not c.is_shortlisted:
                from contracts.decisions import DecisionResult
                results.append(
                    DecisionResult(
                        candidate=c,
                        status="FILTERED_OUT",
                        reason=f"Candidate edge {c.edge:.4f} or confidence {c.confidence:.4f} below hurdle",
                    )
                )
            else:
                res = pipeline.evaluate_candidate(session, c)
                results.append(res)
        session.commit()

    return {
        "status": "success",
        "total_candidates": len(candidates),
        "shortlisted_count": len(shortlisted),
        "decisions": [r.model_dump() for r in results],
    }


@app.get("/api/decisions/history")
def get_decision_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Returns recent persisted LLMDecision analytical records."""
    if not SessionLocal or not LLMDecision:
        return []
    with SessionLocal() as session:
        decisions = (
            session.query(LLMDecision)
            .order_by(LLMDecision.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": str(d.id),
                "event_id": str(d.event_id),
                "llm_version": d.llm_version,
                "verdict": d.verdict,
                "confidence_adjustment": float(d.confidence_adjustment),
                "injury_risk": d.injury_risk,
                "fatigue_risk": d.fatigue_risk,
                "reasoning": d.reasoning,
                "raw_json": d.raw_json,
                "created_at": d.created_at.isoformat(),
            }
            for d in decisions
        ]


@app.post("/api/decisions/strategy-comparison")
def compare_strategies(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Evaluates ML-Only vs ML+LLM outcomes to measure incremental PnL,
    ROI delta, and false positive reduction rate.
    """
    if not LLMStrategyComparator:
        return {"error": "Strategy comparator unavailable"}

    data = payload.get("evaluations", []) if payload else []
    report = LLMStrategyComparator.compare(data)
    return report.model_dump()


@app.post("/api/scheduler/evaluate")
def evaluate_and_enqueue_candidates(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluates raw candidate options, calculates composite candidate scores,
    and enqueues qualifying candidates into the prioritized max-heap queue.
    """
    if not global_candidate_scheduler or not CandidateSelector:
        return {"error": "Scheduler unavailable"}

    raw_options = payload.get("options", [])
    raw_cfg = payload.get("config", {})
    if raw_cfg:
        global_candidate_scheduler.config = CandidateSchedulerConfig(**raw_cfg)

    enqueued = []
    rejected = []

    for opt in raw_options:
        prob = float(opt["model_probability"])
        odds = float(opt["odds"])
        cand = CandidateSelector.evaluate_candidate(
            event_id=str(opt["event_id"]),
            sport_code=opt.get("sport_code", "tennis"),
            participant_a=opt["participant_a"],
            participant_b=opt["participant_b"],
            tournament=opt.get("tournament"),
            market=opt.get("market", "match_winner"),
            selection=opt["selection"],
            odds=odds,
            model_probability=prob,
            confidence=float(opt.get("confidence", 0.6)),
            model_version=opt.get("model_version", "tennis_baseline_v1"),
            min_edge=0.0,
            min_confidence=0.0,
        )

        item = global_candidate_scheduler.evaluate_and_enqueue(
            candidate=cand,
            snapshot_version=opt.get("snapshot_version"),
            score_state=opt.get("score_state"),
        )
        if item:
            enqueued.append(item.model_dump())
        else:
            rejected.append({
                "event_id": cand.event_id,
                "selection": cand.selection,
                "reason": "Composite candidate score below hurdle",
            })

    return {
        "status": "success",
        "total_evaluated": len(raw_options),
        "enqueued_count": len(enqueued),
        "rejected_count": len(rejected),
        "enqueued": enqueued,
        "queue_size": global_candidate_scheduler.priority_queue.size(),
    }


@app.post("/api/scheduler/dispatch")
def dispatch_scheduled_candidates(limit: int = 10) -> Dict[str, Any]:
    """
    Pops top candidates from the priority queue and dispatches them through
    the decision pipeline with snapshot caching, cooldowns, and rate limiting.
    """
    if not global_candidate_scheduler or not SessionLocal or not CombinedDecisionPipeline:
        return {"error": "Scheduler or decision pipeline unavailable"}

    dispatched_results = []
    with SessionLocal() as session:
        pipeline = CombinedDecisionPipeline()
        count = 0
        while count < limit and global_candidate_scheduler.priority_queue.size() > 0:
            item = global_candidate_scheduler.priority_queue.pop()
            if not item:
                break
            res = global_candidate_scheduler.dispatch_candidate(session, item, pipeline)
            dispatched_results.append(res.model_dump())
            count += 1
        session.commit()

    return {
        "status": "success",
        "dispatched_count": len(dispatched_results),
        "remaining_queue_size": global_candidate_scheduler.priority_queue.size(),
        "results": dispatched_results,
    }


@app.post("/api/scheduler/sync-ml-predictions")
def sync_ml_predictions_to_llm(limit: int = 15, dispatch: bool = True) -> Dict[str, Any]:
    """
    Finds live/prematch events that already have ML predictions but no LLM verdict,
    enqueues them into the candidate scheduler, and optionally dispatches through
    CombinedDecisionPipeline so llm_decisions rows are written for the UI.
    """
    if not global_candidate_scheduler or not SessionLocal or not CandidateSelector:
        return {"error": "Scheduler unavailable"}
    if not MLPrediction or not Event or not LLMDecision:
        return {"error": "Prediction models unavailable"}

    from sqlalchemy import and_, exists

    enqueued: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    # Temporarily lower score hurdle so context generation covers more live matches.
    original_min_score = global_candidate_scheduler.config.min_candidate_score
    global_candidate_scheduler.config = global_candidate_scheduler.config.model_copy(
        update={"min_candidate_score": min(original_min_score, 0.35)}
    )

    try:
        with SessionLocal() as session:
            has_llm = exists().where(LLMDecision.event_id == Event.id)
            rows = (
                session.query(Event, MLPrediction)
                .join(MLPrediction, MLPrediction.event_id == Event.id)
                .filter(Event.status.in_(["live", "prematch"]))
                .filter(~has_llm)
                .order_by(Event.is_live.desc(), MLPrediction.created_at.desc())
                .limit(limit * 3)
                .all()
            )

            seen_events = set()
            for evt, pred in rows:
                evt_key = str(evt.id)
                if evt_key in seen_events:
                    continue
                seen_events.add(evt_key)
                if len(enqueued) >= limit:
                    break

                odds = float(pred.bookmaker_odds or 0.0)
                if odds <= 1.0:
                    skipped.append({"event_id": evt_key, "reason": "invalid_odds"})
                    continue

                cand = CandidateSelector.evaluate_candidate(
                    event_id=evt_key,
                    sport_code=evt.sport_code or pred.sport_code or "tennis",
                    participant_a=evt.participant_a_name or "Participant A",
                    participant_b=evt.participant_b_name or "Participant B",
                    tournament=None,
                    market=pred.market or "match_winner",
                    selection=pred.predicted_outcome,
                    odds=odds,
                    model_probability=float(pred.model_probability),
                    confidence=float(pred.confidence or 0.6),
                    model_version=pred.model_version or "unknown",
                    min_edge=0.0,
                    min_confidence=0.0,
                )
                # Always shortlist for qualitative LLM context when ML prediction exists.
                if not cand.is_shortlisted:
                    cand = cand.model_copy(update={"is_shortlisted": True})

                item = global_candidate_scheduler.evaluate_and_enqueue(
                    candidate=cand,
                    snapshot_version=f"mlpred:{pred.id}",
                    score_state=pred.predicted_outcome,
                )
                if item:
                    enqueued.append(
                        {
                            "event_id": evt_key,
                            "selection": cand.selection,
                            "edge": cand.edge,
                            "composite_score": item.score_breakdown.composite_score,
                        }
                    )
                else:
                    skipped.append({"event_id": evt_key, "reason": "score_below_hurdle"})
    finally:
        global_candidate_scheduler.config = global_candidate_scheduler.config.model_copy(
            update={"min_candidate_score": original_min_score}
        )

    dispatch_result: Dict[str, Any] = {"dispatched_count": 0, "results": []}
    if dispatch and enqueued:
        dispatch_result = dispatch_scheduled_candidates(limit=len(enqueued))

    return {
        "status": "success",
        "enqueued_count": len(enqueued),
        "skipped_count": len(skipped),
        "enqueued": enqueued,
        "skipped": skipped[:20],
        "dispatch": dispatch_result,
        "queue_size": global_candidate_scheduler.priority_queue.size(),
    }


@app.get("/api/scheduler/queue")
def get_scheduled_queue() -> List[Dict[str, Any]]:
    """Returns matches waiting for web search or currently being searched."""
    from .research_queue import list_search_queue

    return list_search_queue()


def _is_overdue_live_pbp(row: Dict[str, Any], confirmed_event_ids: set[str], now: datetime) -> bool:
    """Count only runnable live PBP work with a confirmed Flashscore source."""
    if row.get("kind") != "pbp" or row.get("mode") != "live":
        return False
    if row.get("status") not in {"queued", "retry"} or row.get("event_id") not in confirmed_event_ids:
        return False
    retry_at = row.get("next_retry_at")
    if not retry_at:
        return False
    try:
        due_at = retry_at if isinstance(retry_at, datetime) else datetime.fromisoformat(str(retry_at))
    except ValueError:
        return False
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    return due_at <= now


def _flashscore_queue_timing(*, status: str, now: datetime, enqueued_at: datetime | None,
                             next_run_at: datetime | None, attempt_started_at: datetime | None,
                             lease_until: datetime | None) -> Dict[str, Any]:
    """Return status-aware wait, attempt, and lease values for the queue UI."""
    def aware(value):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    due = aware(next_run_at)
    enqueued = aware(enqueued_at)
    started = aware(attempt_started_at)
    lease = aware(lease_until)
    runnable = status in {"queued", "retry"} and (due is None or due <= now)
    waiting_since = due if status == "retry" else enqueued
    return {
        "wait_seconds": max(0, int((now - waiting_since).total_seconds()))
            if runnable and waiting_since else None,
        "attempt_seconds": max(0, int((now - started).total_seconds()))
            if status == "running" and started else None,
        "attempt_started_at": started.isoformat() if started else None,
        "retry_in_seconds": max(0, int((due - now).total_seconds()))
            if status in {"retry", "queued"} and due and due > now else None,
        "lease_until": lease.isoformat() if lease else None,
        "lease_expired": bool(status == "running" and (lease is None or lease <= now)),
    }


_FLASHSCORE_QUEUE_HIDDEN_REASONS = {
    "PLAYER_SOURCE_IDS_MISSING",
    "PLAYER_COUNTRY_CODE_UNRESOLVED",
    "SEARCH_CANDIDATES_REJECTED_BY_NAME",
    # Legacy static profile archive batches have been intentionally disabled.
    # Keep their durable blocked rows and checkpoints for audit/manual import,
    # but do not present them as actionable work in the default queue.
    "STATIC_PROFILE_ARCHIVE_DISABLED_OR_UNVERIFIED",
}


def _flashscore_queue_row_is_hidden(row: dict[str, Any], now: datetime) -> bool:
    """Keep parked work out of the operational table until it can make progress.

    Jobs remain durable in PostgreSQL. A weekly no-match retry becomes visible
    again only when its retry time is due; identity/source blockers reappear
    after their owner clears the reason and requeues the job.
    """
    if row.get("status") == "running":
        return False
    if row.get("status") == "waiting_players":
        # These are durable dependency waits; the dispatcher requeues them when
        # source identities arrive, but they are not actionable queue work.
        return True
    reason = str(row.get("reason") or row.get("last_error") or "")
    if reason == "CAPTCHA_CHALLENGE_ABORTED":
        # Blocked CAPTCHA jobs are parked durable work. They reappear only
        # after Research changes their status back to queued/retry, or an
        # operator explicitly retries them through the existing endpoint.
        if row.get("status") == "blocked":
            return True
        retry_at = row.get("next_retry_at")
        if retry_at:
            try:
                retry_at = retry_at if isinstance(retry_at, datetime) else datetime.fromisoformat(
                    str(retry_at).replace("Z", "+00:00")
                )
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                if retry_at > now:
                    return True
            except (TypeError, ValueError):
                pass
    if reason in _FLASHSCORE_QUEUE_HIDDEN_REASONS:
        return True
    if reason == "PERSISTENT_TRANSPORT_FAILURE":
        # Leave a newly failing source visible for diagnosis, then park stale
        # transport failures so they stop cluttering the operational queue.
        failure_started = (row.get("first_failure_at") or row.get("failure_started_at")
                           or row.get("transport_failure_started_at"))
        if not failure_started:
            # Older durable jobs predate first_failure_at. Once such a job has
            # accumulated repeated attempts, use its creation time as a
            # conservative age anchor instead of resetting the 24h window on
            # every retry/update.
            attempts = int(row.get("attempts") or 0)
            failure_started = (row.get("created_at") if attempts >= 3 else None)
        failure_started = failure_started or row.get("last_activity_at") or row.get("updated_at")
        if failure_started:
            try:
                failure_started = failure_started if isinstance(failure_started, datetime) else datetime.fromisoformat(
                    str(failure_started).replace("Z", "+00:00")
                )
                if failure_started.tzinfo is None:
                    failure_started = failure_started.replace(tzinfo=timezone.utc)
                return now - failure_started >= timedelta(hours=24)
            except (TypeError, ValueError):
                return False
    if reason == "SEARCH_COMPLETED_NO_MATCH":
        status = row.get("status")
        # A no-match result is durable negative evidence. Keep a parked job out
        # of the operational table until its weekly retry is actionable. Legacy
        # blocked rows need an explicit data/identity change to become runnable.
        if status in {"blocked", "waiting_players", "waiting_match"}:
            return True
        if status not in {"retry", "queued"}:
            return False
        retry_at = row.get("next_retry_at")
        if retry_at:
            try:
                retry_at = retry_at if isinstance(retry_at, datetime) else datetime.fromisoformat(str(retry_at))
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return retry_at > now
            except (TypeError, ValueError):
                return True
        # A retry/queued row without a durable due time cannot be dispatched
        # safely, so it should not look actionable in the table.
        return True
    return False


def _flashscore_queue_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Put stale-live lookups after all actionable Flashscore work."""
    status_rank = {"running": 0, "queued": 1, "retry": 2, "blocked": 3,
                   "waiting_players": 4, "waiting_match": 4,
                   "completed": 5, "finalized": 5, "skipped": 6}
    stale_live = (row.get("reason") or row.get("last_error")) == "EVENT_NO_LONGER_FRESH_LIVE"
    return (1 if stale_live else 0, status_rank.get(row.get("status"), 4),
            row.get("priority", 10), row.get("next_retry_at") or "", row.get("id", ""))


_TENNIS_SOURCE_CAPABILITIES = {
    "match_history": {"tennis_my_life", "tennis_explorer", "tennis_abstract", "api_tennis", "flashscore"},
    "serve_statistics": {"tennis_my_life", "api_tennis", "flashscore"},
    "point_by_point": {"api_tennis", "match_charting_project", "flashscore"},
}


def _tennis_source_capability_enabled(source: str, capability: str) -> bool:
    canonical_source = {"mcp": "match_charting_project", "tml": "tennis_my_life"}.get(source, source)
    if canonical_source not in _TENNIS_SOURCE_CAPABILITIES.get(capability, set()):
        return False
    source_key = canonical_source.upper().replace("-", "_")
    value = os.getenv(f"TENNIS_SOURCE_{source_key}_{capability.upper()}_ENABLED", "true")
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _queue_player_source_context(profile, coverage_rows, *, now: datetime,
                                missing_pbp_identity: bool = False) -> dict[str, Any]:
    """Summarize the latest per-capability evidence for a queue row."""
    sources = {}
    for coverage in coverage_rows:
        coverage_source = {"mcp": "match_charting_project", "tml": "tennis_my_life"}.get(
            coverage.source, coverage.source
        )
        if not _tennis_source_capability_enabled(coverage_source, coverage.capability):
            continue
        checked = coverage.checked_at
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        checked = checked.astimezone(timezone.utc)
        age = now - checked
        fresh = timedelta(0) <= age <= timedelta(days=7)
        partial_sources = {
            "match_history": {"api_tennis", "tennis_explorer", "tennis_abstract"},
            "serve_statistics": {"tennis_my_life", "api_tennis"},
        }
        partial_capability = (
            coverage.capability in partial_sources
            and coverage_source in partial_sources[coverage.capability]
            and coverage.status == "partial" and not coverage.complete
            and (coverage.evidence_json or {}).get("usable_for_metrics") is True
        )
        usable = fresh and ((coverage.status == "available" and coverage.complete) or partial_capability)
        current = sources.get(coverage.capability)
        if current is None or checked > current["_checked_at"]:
            sources[coverage.capability] = {
                "source": coverage_source, "status": coverage.status,
                "complete": bool(coverage.complete), "checked_at": checked.isoformat(),
                "evidence": dict(coverage.evidence_json or {}),
                "fresh": fresh, "usable": usable, "_checked_at": checked,
            }
    for value in sources.values():
        value.pop("_checked_at", None)
    identities = dict(getattr(profile, "source_identities", None) or {}) if profile else {}
    confirmed_ids = {
        key.removeprefix("archive:"): str(value)
        for key, value in identities.items()
        if value and (key.startswith("archive:") or key == "flashscore")
    }
    history = sources.get("match_history")
    fallback_reason = (
        "IDENTITY_CONFIRMATION_REQUIRED"
        if profile and getattr(profile, "sync_status", None) == "identity_confirmation_required"
        else "PBP_IDENTITY_MISSING_HISTORY_AVAILABLE"
        if missing_pbp_identity and history and history["usable"]
        else "PBP_IDENTITY_MISSING"
        if missing_pbp_identity
        else "FRESH_HISTORY_AVAILABLE" if history and history["usable"]
        else "HISTORY_SOURCE_GAP_OR_STALE"
    )
    return {"confirmed_source_ids": confirmed_ids, "capabilities": sources,
            "fallback_reason": fallback_reason,
            "required_capability": "point_by_point" if missing_pbp_identity else "match_history"}


@app.get("/api/tennis-history/flashscore/queue")
def get_flashscore_research_queue(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    kind: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    mode: Optional[str] = Query(None, description="live or prematch"),
    search: Optional[str] = None,
    include_completed: bool = False,
    include_parked: bool = False,
) -> Dict[str, Any]:
    """Read-only operational view of durable Flashscore jobs."""
    from db.models import FlashscoreImportJob, TennisMatchLookupJob, TennisPlayerRefreshJob, TennisPointDocument, TennisPointRefreshJob
    from db.models.external_tennis import (
        TennisPlayerDataCoverage, TennisPlayerProfile, TennisPointMatchLink,
    )
    from db.models.sports import Participant

    now = datetime.now(timezone.utc)
    active_cutoff = now - timedelta(seconds=90)
    with SessionLocal() as session:
        event_rows = session.query(Event).filter(Event.sport_code == "tennis", Event.finished_at.is_(None)).filter(
            ~Event.participant_a_name.contains("/"), ~Event.participant_b_name.contains("/"),
        ).filter(
            (Event.is_live.is_(True) & (Event.last_seen_at >= active_cutoff)) |
            (Event.is_live.is_(False) & (Event.scheduled_start_at >= now) & (Event.scheduled_start_at <= now + timedelta(hours=6)))
        ).all()
        eligible_ids = {str(event.id) for event in event_rows}
        event_map = {str(event.id): event for event in event_rows}
        event_ids = [event.id for event in event_rows]
        active_participant_ids = {
            participant_id
            for event in event_rows
            for participant_id in (event.participant_a_id, event.participant_b_id)
            if participant_id
        }
        rows: list[dict[str, Any]] = []

        terminal_point_statuses = ("completed", "finalized", "skipped", "superseded")
        # The queue is an operational view.  A live event can retain a finished
        # PBP job, but it must not make that job appear as pending work unless
        # the caller explicitly asks for the recent completion history.
        point_scope = [and_(
            TennisPointRefreshJob.event_id.in_(event_ids),
            ~TennisPointRefreshJob.status.in_(terminal_point_statuses),
        )]
        if include_completed:
            point_scope.append(and_(TennisPointRefreshJob.status.in_(terminal_point_statuses),
                                    TennisPointRefreshJob.updated_at >= now - timedelta(hours=24)))
        point_jobs = session.query(TennisPointRefreshJob).filter(or_(*point_scope)).order_by(
            TennisPointRefreshJob.updated_at.desc()
        ).all()
        for job in point_jobs:
            event = event_map.get(str(job.event_id))
            if event is None and not (include_completed and job.status in {"completed", "finalized", "skipped", "superseded"} and job.updated_at >= now - timedelta(hours=24)):
                continue
            event = event or session.get(Event, job.event_id)
            if event is None:
                continue
            event_mode = "live" if event.is_live else "prematch"
            rows.append({"id": f"pbp:{job.event_id}", "event_id": str(job.event_id),
                "label": f"{event.participant_a_name} — {event.participant_b_name}", "player": None,
                "kind": "pbp", "status": job.status, "priority": 0 if event.is_live else 10,
                "mode": event_mode, **_flashscore_queue_timing(status=job.status, now=now,
                    enqueued_at=job.last_requested_at, next_run_at=job.next_run_at,
                    attempt_started_at=job.attempt_started_at or (job.updated_at if job.status == "running" else None),
                    lease_until=job.lease_until),
                "attempts": job.attempts, "last_activity_at": job.updated_at.isoformat(),
                "last_success_at": job.updated_at.isoformat() if job.status in {"completed", "finalized"} else None,
                "next_retry_at": job.next_run_at.isoformat(),
                "reason": None if job.status == "running" else job.last_error,
                "last_error": job.last_error, "related_events": 1})

        terminal_match_statuses = ("matched", "waiting_confirmation")
        stale_live_match_rows = session.query(TennisMatchLookupJob.event_id).filter(
            TennisMatchLookupJob.status == "waiting_match",
            TennisMatchLookupJob.last_error == "EVENT_NO_LONGER_FRESH_LIVE",
            TennisMatchLookupJob.updated_at >= now - timedelta(hours=24),
        ).all()
        match_event_ids = set(event_ids) | {row[0] for row in stale_live_match_rows}
        match_scope = [and_(
            TennisMatchLookupJob.event_id.in_(match_event_ids or [uuid.UUID(int=0)]),
            ~TennisMatchLookupJob.status.in_(terminal_match_statuses),
        )]
        if include_completed:
            match_scope.append(and_(TennisMatchLookupJob.status.in_(terminal_match_statuses),
                                    TennisMatchLookupJob.updated_at >= now - timedelta(hours=24)))
        match_jobs = session.query(TennisMatchLookupJob).filter(or_(*match_scope)).order_by(
            TennisMatchLookupJob.updated_at.desc()
        ).all()
        for job in match_jobs:
            event = event_map.get(str(job.event_id))
            stale_live_lookup = job.last_error == "EVENT_NO_LONGER_FRESH_LIVE" and job.status == "waiting_match"
            stale_live_deferred = job.last_error == "EVENT_NO_LONGER_FRESH_LIVE"
            if event is None and not (stale_live_lookup or (include_completed and job.status in {"matched", "waiting_confirmation"} and job.updated_at >= now - timedelta(hours=24))):
                continue
            event = event or session.get(Event, job.event_id)
            if event is None:
                continue
            # waiting_match is a dependency wait until the Fonbet event is
            # fresh live again, not a scheduled retry. Keep its old next_run_at
            # internally for recovery, but do not present it as an attempt ETA.
            next_retry_at = None if stale_live_lookup else job.next_run_at.isoformat()
            rows.append({"id": f"match:{job.event_id}", "event_id": str(job.event_id),
                "label": f"{event.participant_a_name} — {event.participant_b_name}", "player": None,
                "kind": "match_lookup", "status": job.status,
                "priority": 999 if stale_live_deferred else (1 if event.is_live else 11),
                "mode": "live" if event.is_live else "prematch",
                **_flashscore_queue_timing(status=job.status, now=now,
                    enqueued_at=job.updated_at, next_run_at=None if stale_live_lookup else job.next_run_at,
                    attempt_started_at=job.attempt_started_at or (job.updated_at if job.status == "running" else None),
                    lease_until=job.lease_until),
                "attempts": job.attempts, "last_activity_at": job.updated_at.isoformat(),
                "last_success_at": job.updated_at.isoformat() if job.status == "matched" else None,
                "next_retry_at": next_retry_at,
                "reason": None if job.status == "running" else job.last_error,
                "last_error": job.last_error, "related_events": 1})

        player_scope = [and_(
            Participant.id.in_(active_participant_ids),
            TennisPlayerRefreshJob.status != "completed",
        )]
        if include_completed:
            player_scope.append(and_(TennisPlayerRefreshJob.status == "completed",
                                     TennisPlayerRefreshJob.updated_at >= now - timedelta(hours=24)))
        player_jobs = session.query(TennisPlayerRefreshJob, Participant, TennisPlayerProfile).join(
            Participant, Participant.id == TennisPlayerRefreshJob.participant_id
        ).outerjoin(TennisPlayerProfile, TennisPlayerProfile.participant_id == Participant.id).filter(
            Participant.sport_code == "tennis", or_(*player_scope)
        ).order_by(TennisPlayerRefreshJob.requested_at.desc()).all()
        coverage_by_participant: dict[str, list[Any]] = {}
        player_ids = list({job.participant_id for job, _participant, _profile in player_jobs})
        if player_ids:
            coverage_rows = session.query(TennisPlayerDataCoverage).filter(
                TennisPlayerDataCoverage.participant_id.in_(player_ids)
            ).all()
            for coverage in coverage_rows:
                coverage_by_participant.setdefault(str(coverage.participant_id), []).append(coverage)
        participant_events: dict[str, list[Any]] = {}
        for event in event_map.values():
            for pid in (event.participant_a_id, event.participant_b_id):
                if pid:
                    participant_events.setdefault(str(pid), []).append(event)
        missing_pbp_identity_players: set[str] = set()
        for point_job in point_jobs:
            if point_job.status != "waiting_players" or point_job.last_error != "PLAYER_SOURCE_IDS_MISSING":
                continue
            event = event_map.get(str(point_job.event_id))
            if event is not None:
                missing_pbp_identity_players.update(
                    str(pid) for pid in (event.participant_a_id, event.participant_b_id) if pid
                )
        for job, participant, profile in player_jobs:
            associated = participant_events.get(str(participant.id), [])
            if not associated and not (include_completed and job.status == "completed"):
                continue
            display_status = "identity_confirmation_required" if profile and profile.sync_status == "identity_confirmation_required" else job.status
            source_context = _queue_player_source_context(
                profile, coverage_by_participant.get(str(participant.id), []), now=now,
                missing_pbp_identity=str(participant.id) in missing_pbp_identity_players,
            )
            rows.append({"id": f"player:{participant.id}", "event_id": str(associated[0].id) if associated else None,
                "related_event_ids": [str(event.id) for event in associated],
                "label": " · ".join(dict.fromkeys(f"{e.participant_a_name} — {e.participant_b_name}" for e in associated[:3])) or participant.canonical_name,
                "player": participant.canonical_name, "kind": "player_history", "status": display_status,
                "priority": 30, "mode": "live" if any(e.is_live for e in associated) else "prematch",
                **_flashscore_queue_timing(status=job.status, now=now,
                    enqueued_at=job.requested_at, next_run_at=job.next_run_at,
                    attempt_started_at=job.attempt_started_at or (job.updated_at if job.status == "running" else None),
                    lease_until=job.lease_until),
                "attempts": job.attempts, "last_activity_at": job.updated_at.isoformat(),
                "last_success_at": profile.last_success_at.isoformat() if profile and profile.last_success_at else None,
                "next_retry_at": job.next_run_at.isoformat() if (
                    job.status in {"retry", "queued"}
                    or job.last_error == "CAPTCHA_CHALLENGE_ABORTED"
                ) else None,
                "reason": None if job.status == "running" else job.last_error,
                "last_error": job.last_error, "related_events": len(associated),
                "source_context": source_context})

        imports = session.query(FlashscoreImportJob).order_by(FlashscoreImportJob.updated_at.desc()).limit(200).all()
        for job in imports:
            if job.status in {"completed", "finalized", "skipped", "superseded"} and (not include_completed or job.updated_at < now - timedelta(hours=24)):
                continue
            rows.append({"id": f"archive:{job.id}", "event_id": None, "label": ", ".join(job.profile_urls or [])[:180],
                "player": None, "kind": "full_archive", "status": job.status, "priority": 20,
                "mode": "archive", **_flashscore_queue_timing(status=job.status, now=now,
                    enqueued_at=job.created_at, next_run_at=job.next_run_at,
                    attempt_started_at=job.attempt_started_at or (job.updated_at if job.status == "running" else None),
                    lease_until=job.lease_until),
                "attempts": job.attempts, "last_activity_at": job.updated_at.isoformat(),
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "first_failure_at": (job.progress_json or {}).get("first_failure_at"),
                "last_success_at": job.updated_at.isoformat() if job.status == "completed" else None,
                "next_retry_at": job.next_run_at.isoformat(),
                "reason": None if job.status == "running" else job.last_error,
                "last_error": job.last_error, "related_events": 0})

        link_history = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.fonbet_event_id.is_not(None), TennisPointMatchLink.available_at <= now
        ).distinct(TennisPointMatchLink.source_match_id).order_by(
            TennisPointMatchLink.source_match_id,
            TennisPointMatchLink.available_at.desc(),
            TennisPointMatchLink.id.desc(),
        ).all()
        latest_by_source = {}
        for link in link_history:
            latest_by_source[link.source_match_id] = link
        confirmed_event_ids = {str(link.fonbet_event_id) for link in latest_by_source.values()
                               if link.fonbet_event_id and link.status in {"matched", "manual"}}
        overdue_pbp = sum(1 for row in rows if _is_overdue_live_pbp(row, confirmed_event_ids, now))

        if kind:
            rows = [row for row in rows if row["kind"] == kind]
        if status_filter:
            rows = [row for row in rows if row["status"] == status_filter]
        if mode in {"live", "prematch"}:
            rows = [row for row in rows if row["mode"] == mode]
        if search:
            needle = search.casefold()
            rows = [row for row in rows if needle in (row.get("label") or "").casefold() or needle in (row.get("player") or "").casefold()]
        if not include_parked:
            rows = [row for row in rows if not _flashscore_queue_row_is_hidden(row, now)]
        rows.sort(key=_flashscore_queue_sort_key)
        summary_statuses = {name: sum(1 for row in rows if row["status"] == name)
                            for name in {row["status"] for row in rows}}
        identity_confirmation = sum(1 for row in rows if row["status"] == "identity_confirmation_required")
        total = len(rows)
        page = rows[offset:offset + limit]
        statuses = {name: sum(1 for row in rows if row["status"] == name) for name in {row["status"] for row in rows}}
        profiles_ready = session.query(func.count(TennisPlayerProfile.participant_id)).filter(
            TennisPlayerProfile.participant_id.in_({pid for e in event_map.values() for pid in (e.participant_a_id, e.participant_b_id) if pid}),
            TennisPlayerProfile.metrics_version > 0,
            TennisPlayerProfile.sync_status.in_(("available", "empty", "stale")),
        ).scalar() if event_map else 0
        hour_ago = now - timedelta(hours=1)
        documents_saved_last_hour = session.query(func.count(TennisPointDocument.id)).filter(
            TennisPointDocument.retrieved_at >= hour_ago).scalar() or 0
        confirmed_links_last_hour = session.query(func.count(TennisPointMatchLink.id)).filter(
            TennisPointMatchLink.available_at >= hour_ago,
            TennisPointMatchLink.status.in_(("matched", "manual"))).scalar() or 0
        worker_state = {"configured": True, "healthy": False, "last_heartbeat_at": None, "network_slots": 0, "network_slots_limit": 2, "cooldown_until": None}
        try:
            source_control = session.execute(text("SELECT slot_limit, cooldown_until, executor_heartbeat_at FROM flashscore_source_control WHERE id=1")).mappings().first()
            if source_control:
                worker_state["network_slots_limit"] = int(source_control["slot_limit"])
                worker_state["cooldown_until"] = source_control["cooldown_until"].isoformat() if source_control["cooldown_until"] else None
                heartbeat_at = source_control["executor_heartbeat_at"]
                worker_state["last_heartbeat_at"] = heartbeat_at.isoformat() if heartbeat_at else None
                worker_state["healthy"] = bool(heartbeat_at and heartbeat_at >= now - timedelta(seconds=45))
                worker_state["network_slots"] = int(session.execute(text("SELECT count(*) FROM flashscore_source_requests WHERE acquired_at IS NOT NULL AND lease_until > now() AND heartbeat_at >= now()-interval '15 seconds'")).scalar() or 0)
        except Exception:
            # Queue visibility still works when the worker-control migration has
            # not yet been deployed; the empty control status remains explicit.
            worker_state["configured"] = False
        browser_budget = int(os.getenv(
            "FLASHSCORE_WORKER_BROWSER_PROCESSES",
            os.getenv("FLASHSCORE_BROWSER_PROCESSES", "2"),
        ))
        browser_budget = max(1, min(4, browser_budget))
        live_capacity = max(1, min(browser_budget - 1,
            int(os.getenv("FLASHSCORE_WORKER_TENNIS_POINT_CONCURRENCY", "1"))))
        current_capacity = 1 if browser_budget > live_capacity else 0
        backlog_capacity = max(0, browser_budget - live_capacity - current_capacity)
        lane_profiles_current = session.query(func.count(TennisPlayerRefreshJob.participant_id)).filter(
            TennisPlayerRefreshJob.participant_id.in_(active_participant_ids or {uuid.UUID(int=0)}),
            TennisPlayerRefreshJob.status == "running",
            TennisPlayerRefreshJob.lease_until > now,
        ).scalar() or 0
        lane_match_search = session.query(func.count(TennisMatchLookupJob.event_id)).filter(
            TennisMatchLookupJob.event_id.in_(event_ids or {uuid.UUID(int=0)}),
            TennisMatchLookupJob.status == "running",
            TennisMatchLookupJob.lease_until > now,
        ).scalar() or 0
        lane_live_pbp = session.query(func.count(TennisPointRefreshJob.event_id)).filter(
            TennisPointRefreshJob.event_id.in_(
                [e.id for e in event_rows if e.is_live] or {uuid.UUID(int=0)}
            ),
            TennisPointRefreshJob.status == "running",
            TennisPointRefreshJob.lease_until > now,
        ).scalar() or 0
        from db.models import TennisPointSourceRefreshJob
        lane_source_pbp = session.query(func.count(TennisPointSourceRefreshJob.source_match_id)).filter(
            TennisPointSourceRefreshJob.status == "running",
            TennisPointSourceRefreshJob.lease_until > now,
        ).scalar() or 0
        lane_archives = session.query(func.count(FlashscoreImportJob.id)).filter(
            FlashscoreImportJob.status == "running",
            FlashscoreImportJob.lease_until > now,
        ).scalar() or 0
        lane_profiles_background_query = session.query(func.count(TennisPlayerRefreshJob.participant_id)).filter(
            TennisPlayerRefreshJob.status == "running",
            TennisPlayerRefreshJob.lease_until > now,
        )
        if active_participant_ids:
            lane_profiles_background_query = lane_profiles_background_query.filter(
                ~TennisPlayerRefreshJob.participant_id.in_(active_participant_ids)
            )
        lane_profiles_background = lane_profiles_background_query.scalar() or 0
        profile_slot_capacity = max(1, int(os.getenv("FLASHSCORE_WORKER_PROFILE_CONCURRENCY", "2")))
        worker_state["browser_lanes"] = {
            "total": browser_budget,
            "live_pbp": {"capacity": live_capacity, "active": int(lane_live_pbp)},
            "current_matches": {"capacity": current_capacity,
                                "active": int(lane_profiles_current + lane_match_search)},
            "backlog": {"capacity": backlog_capacity,
                        "active": int(lane_source_pbp + lane_archives + lane_profiles_background),
                        "shared_with_current": backlog_capacity == 0},
        }
        worker_state["player_history_slots"] = {
            "capacity": profile_slot_capacity,
            "active": int(lane_profiles_current + lane_profiles_background),
        }
        return {"items": page, "total": total, "limit": limit, "offset": offset,
            "generated_at": now.isoformat(), "eligible_matches": len(eligible_ids),
            "prepared_players": int(profiles_ready or 0), "statuses": summary_statuses,
            "identity_confirmation_required": identity_confirmation, "overdue_pbp": overdue_pbp,
            "documents_saved_last_hour": int(documents_saved_last_hour),
            "confirmed_links_last_hour": int(confirmed_links_last_hour),
            "worker": worker_state}


@app.post("/api/tennis-history/flashscore/import/{job_id}/retry")
def retry_blocked_flashscore_import(job_id: uuid.UUID) -> Dict[str, Any]:
    """Explicitly retry a parked archive after access or identity has changed."""
    from db.models import FlashscoreImportJob

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        job = session.query(FlashscoreImportJob).filter_by(id=job_id).with_for_update().first()
        if job is None:
            raise HTTPException(status_code=404, detail="Flashscore import job not found")
        if job.status != "blocked":
            raise HTTPException(status_code=409, detail=f"Job is {job.status}, not blocked")
        progress = dict(job.progress_json or {})
        history = list(progress.get("manual_retries") or [])
        history.append({"at": now.isoformat(), "previous_error": job.last_error,
                        "attempts_before_retry": job.attempts})
        progress["manual_retries"] = history[-20:]
        job.progress_json = progress
        job.status = "queued"
        job.last_error = None
        job.next_run_at = now
        job.attempt_token = None
        job.lease_until = None
        job.updated_at = now
        session.commit()
        return {"status": job.status, "job_id": str(job.id), "attempts": job.attempts,
                "progress": job.progress_json, "previous_error": history[-1]["previous_error"]}


@app.post("/api/tennis-history/flashscore/player/{participant_id}/retry")
def retry_blocked_flashscore_player(participant_id: uuid.UUID) -> Dict[str, Any]:
    """Explicitly retry a player refresh parked by a Flashscore CAPTCHA."""
    from db.models import TennisPlayerRefreshJob

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        job = session.query(TennisPlayerRefreshJob).filter_by(
            participant_id=participant_id
        ).with_for_update().first()
        if job is None:
            raise HTTPException(status_code=404, detail="Flashscore player job not found")
        if job.status != "blocked" or job.last_error != "CAPTCHA_CHALLENGE_ABORTED":
            raise HTTPException(status_code=409, detail="Player job is not parked for CAPTCHA")
        progress = dict(job.progress_json or {})
        history = list(progress.get("manual_retries") or [])
        history.append({"at": now.isoformat(), "previous_error": job.last_error,
                        "attempts_before_retry": job.attempts})
        progress["manual_retries"] = history[-20:]
        job.progress_json = progress
        job.status = "queued"
        job.last_error = None
        job.next_run_at = now
        job.attempt_token = None
        job.lease_until = None
        job.completed_at = None
        job.updated_at = now
        session.commit()
        return {"status": job.status, "participant_id": str(participant_id),
                "attempts": job.attempts, "progress": job.progress_json,
                "previous_error": history[-1]["previous_error"]}


def _summarize_tennis_source_metrics(payload: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    cutoff = now - timedelta(hours=24)
    totals = {"requests": 0, "successful_requests": 0, "errors": 0, "published": 0,
              "prevented_flashscore": 0, "latency_total_ms": 0, "latency_samples": 0}
    max_latency_ms = 0
    for key, bucket in ((payload or {}).get("buckets") or {}).items():
        try:
            bucket_at = datetime.fromisoformat(str(key).replace("Z", "+00:00"))
            if bucket_at.tzinfo is None:
                bucket_at = bucket_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if bucket_at < cutoff or bucket_at > now:
            continue
        for field in totals:
            value = bucket.get(field, 0)
            if isinstance(value, (int, float)):
                totals[field] += int(value)
        max_latency_ms = max(max_latency_ms, int(bucket.get("latency_max_ms", 0) or 0))
    samples = totals.pop("latency_samples")
    latency_total = totals.pop("latency_total_ms")
    totals.update({"window": "rolling_24h",
                   "avg_latency_ms": round(latency_total / samples) if samples else None,
                   "max_latency_ms": max_latency_ms})
    return totals


def _api_tennis_offline_import_status(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Expose durable progress without exposing raw provider payloads."""
    fields = (
        "status", "rows_scanned", "documents_saved", "projected", "merged",
        "ambiguous", "unlinked", "unchanged", "cursor_version",
        "last_available_at", "last_source_match_id", "paused_at", "checkpointed_at", "completed_at",
    )
    result = {}
    for key in ("pbp_backfill", "canonical_projection_backfill"):
        snapshot = state.get(key) or {}
        result[key] = {field: snapshot.get(field) for field in fields if field in snapshot}
        result[key].setdefault("status", "not_started")
    return result


def _live_tennis_profile_status(state: dict[str, Any], *, now: datetime) -> tuple[str, dict[str, Any]]:
    profile_states = [value for key, value in state.items()
                      if key.startswith("profile:") and isinstance(value, dict)]
    successful_profiles = [value for value in profile_states
                           if value.get("status") == 200 and isinstance(value.get("profile"), dict)]
    fresh_profiles = 0
    for profile_state in successful_profiles:
        checked = profile_state.get("checked_at") or profile_state.get("observed_at")
        if not checked:
            continue
        try:
            checked_at = datetime.fromisoformat(str(checked).replace("Z", "+00:00"))
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=timezone.utc)
            if timedelta(0) <= now - checked_at.astimezone(timezone.utc) <= timedelta(days=7):
                fresh_profiles += 1
        except (TypeError, ValueError):
            continue
    catalog = state.get("latest_live_catalog") or {}
    status = (
        "live_catalog_and_profiles" if catalog.get("status") == 200 and successful_profiles
        else "live_catalog_supplement" if catalog.get("status") == 200
        else "profiles_available" if successful_profiles
        else "credentials_ready"
    )
    profile_status = "fresh_profiles_available" if fresh_profiles else (
        "profiles_stale" if successful_profiles else "profiles_not_yet_fetched"
    )
    latest_attempt = max(
        profile_states,
        key=lambda item: str(item.get("observed_at") or item.get("checked_at") or ""),
        default=None,
    )
    last_attempt = None
    if latest_attempt is not None:
        reason = latest_attempt.get("error") or (
            latest_attempt.get("match_status") if latest_attempt.get("status") != 200 else None
        )
        last_attempt = {
            "status": str(latest_attempt.get("status") or "unknown"),
            "reason": str(reason) if reason else None,
            "observed_at": latest_attempt.get("observed_at") or latest_attempt.get("checked_at"),
            "retry_after": latest_attempt.get("retry_after"),
        }
    summary = {
        "status": profile_status,
        "profiles_with_snapshot": len(successful_profiles),
        "fresh_snapshots": fresh_profiles,
        "attempted_profiles": len(profile_states),
        "last_attempt": last_attempt,
        "complete_career_history": False,
        "role": "public_bio_and_ranking_supplement",
        "match_data_authority": "fonbet",
    }
    return status, summary


def _tennis_explorer_profile_daily_limit(profile_quota: dict[str, Any]) -> int:
    """Read the configured profile-fetch budget persisted by the source worker."""
    try:
        return max(0, int(profile_quota.get("daily_limit", 20)))
    except (TypeError, ValueError):
        return 20


@app.get("/api/tennis-history/sources/status")
def get_tennis_source_status() -> Dict[str, Any]:
    """Operational source/capability view; never returns credentials or request URLs with secrets."""
    sources = {
        "tennis_my_life": {"label": "TennisMyLife", "role": "primary", "capabilities": ["results", "serve_statistics"], "tours": ["ATP", "WTA", "Challenger"]},
        "tennis_explorer": {"label": "TennisExplorer", "role": "recent_results_supplement", "capabilities": ["results", "accessible_profile_history"], "tours": ["ATP", "WTA", "Challenger", "ITF"], "profile_tours": ["ITF"]},
        "tennis_abstract": {"label": "Tennis Abstract", "role": "partial_history_supplement", "capabilities": ["partial_recent_results", "canonical_partial_history", "provider_percentages_not_metric_ready"], "tours": ["ATP", "WTA"]},
        "api_tennis": {"label": "API-Tennis", "role": "temporary_archive_supplement", "capabilities": ["results", "partial_history", "partial_pbp", "serve_statistics"], "tours": ["ATP", "WTA", "Challenger", "ITF"]},
        "live_tennis_api": {"label": "Live Tennis API", "role": "catalog_and_profile_supplement", "capabilities": ["match_catalog", "public_player_profiles"], "tours": ["ATP", "WTA", "Challenger", "ITF"]},
        "livescore": {"label": "LiveScore", "role": "match_discovery_supplement", "capabilities": ["live_match_discovery"], "tours": ["ATP", "WTA", "Challenger"]},
        "mcp": {"label": "Match Charting Project", "role": "historical_supplement", "capabilities": ["selected_pbp", "shot_statistics"], "tours": ["ATP", "WTA"]},
        "stored_profile_metrics": {"label": "Сохранённая история Neurobet", "role": "local_history_cache", "capabilities": ["stored_match_history"], "tours": ["ATP", "WTA", "Challenger", "ITF"]},
        "flashscore": {"label": "Flashscore", "role": "fallback", "capabilities": ["history", "serve_statistics", "pbp"], "tours": ["ATP", "WTA", "Challenger", "ITF"]},
    }
    state_by_source: dict[str, dict[str, Any]] = {}
    job_counts: dict[str, dict[str, int]] = {}
    point_counts: dict[str, dict[str, int]] = {}
    player_coverage: dict[str, list[dict[str, Any]]] = {}
    now = datetime.now(timezone.utc)
    try:
        with SessionLocal() as session:
            state_rows = session.execute(text("""
                SELECT source,state_key,state_json,updated_at
                FROM external_tennis_source_state
                WHERE source IN ('permanent_sources','api_tennis','live_tennis_api','livescore','match_charting_project','tennis_my_life','tennis_explorer','tennis_explorer_profiles','tennis_abstract','stored_profile_metrics')
            """)).mappings().all()
            for row in state_rows:
                state = row["state_json"] if isinstance(row["state_json"], dict) else {}
                if row["source"] == "permanent_sources" and row["state_key"] == "status":
                    for name, value in (state.get("integrations") or {}).items():
                        state_by_source.setdefault(name, {})["status"] = value
                elif row["source"] == "tennis_explorer_profiles" and row["state_key"] == "quota":
                    state_by_source.setdefault("tennis_explorer", {})["profile_quota"] = state
                elif row["state_key"] == "request_metrics":
                    source_key = "mcp" if row["source"] == "match_charting_project" else row["source"]
                    state_by_source.setdefault(source_key, {})["request_metrics"] = state
                elif row["source"] in sources or row["source"] == "match_charting_project":
                    source_key = "mcp" if row["source"] == "match_charting_project" else row["source"]
                    state_by_source.setdefault(source_key, {})[row["state_key"]] = state
                    state_by_source[source_key]["updated_at"] = row["updated_at"].isoformat() if row["updated_at"] else None
            charting_status = state_by_source.pop("match_charting_project", None)
            if charting_status:
                state_by_source.setdefault("mcp", {}).update(charting_status)
            if "api_tennis_archive" in state_by_source:
                api_status = state_by_source.pop("api_tennis_archive")
                state_by_source.setdefault("api_tennis", {}).update(api_status)
            for row in session.execute(text("""
                SELECT source,status,count(*) AS total
                FROM external_tennis_source_jobs GROUP BY source,status
            """)).mappings():
                job_counts.setdefault(row["source"], {})[row["status"]] = int(row["total"])
            for row in session.execute(text("""
                SELECT source,parse_status,count(*) AS total
                FROM tennis_point_documents GROUP BY source,parse_status
            """)).mappings():
                point_counts.setdefault(row["source"], {})[row["parse_status"]] = int(row["total"])
            point_counts["mcp"] = point_counts.get("match_charting_project", {})
            cutoff = now - timedelta(days=7)
            for row in session.execute(text("""
                SELECT source,capability,status,complete,count(*) AS profiles,
                       count(*) FILTER (WHERE checked_at >= :cutoff AND checked_at <= :now) AS fresh_profiles,
                       count(*) FILTER (
                         WHERE checked_at >= :cutoff AND checked_at <= :now
                           AND ((status='available' AND complete IS TRUE)
                             OR (capability IN ('match_history','serve_statistics')
                                 AND status='partial'
                                 AND complete IS FALSE AND evidence_json->>'usable_for_metrics'='true'))
                       ) AS usable_profiles,
                       max(checked_at) AS latest_checked_at
                FROM tennis_player_data_coverage
                GROUP BY source,capability,status,complete
                ORDER BY source,capability,status,complete
            """), {"cutoff": cutoff, "now": now}).mappings():
                player_coverage.setdefault(row["source"], []).append({
                    "capability": row["capability"], "status": row["status"],
                    "complete": bool(row["complete"]), "profiles": int(row["profiles"]),
                    "fresh_profiles": int(row["fresh_profiles"]),
                    "usable_profiles": int(row["usable_profiles"]),
                    "latest_checked_at": row["latest_checked_at"].isoformat()
                    if row["latest_checked_at"] else None,
                })
    except Exception as exc:
        logger.exception("Tennis source status query failed: %s", type(exc).__name__)
        return {"generated_at": now.isoformat(), "status": "partial", "error": "source_status_unavailable", "sources": []}

    result = []
    for source, metadata in sources.items():
        state = state_by_source.get(source, {})
        gate_source = {"mcp": "match_charting_project"}.get(source, source)
        route_capabilities = [capability for capability, providers in _TENNIS_SOURCE_CAPABILITIES.items()
                              if gate_source in providers]
        quota_state = state.get("quota") or {}
        quota = None
        if quota_state:
            daily_budget, minute_budget = ((6000, 30) if source == "api_tennis" else
                                           (80, 24) if source == "live_tennis_api" else
                                           (24, 1) if source == "livescore" else
                                           (10, None) if source == "tennis_abstract" else (None, None))
            quota = {"day": quota_state.get("day"), "daily_used": quota_state.get("daily_calls"),
                     "daily_limit": daily_budget, "minute_used": quota_state.get("minute_calls"),
                     "minute_limit": minute_budget}
        status = (
            "local_history_cache" if source == "stored_profile_metrics" else
            state.get("status") or ("fallback_ready" if source == "flashscore" else "not_integrated")
        )
        if source == "tennis_my_life" and state.get("historical_archive"):
            archive = state["historical_archive"]
            status = (f"historical_seasons_{archive.get('status', 'running')}_"
                      f"{archive.get('next_partition', 0)}_of_{archive.get('partitions_total', 0)}")
        if source == "tennis_explorer":
            recent = state.get("recent_window") or {}
            if recent.get("status") == 200:
                status = "recent_results_ingested_player_career_unverified"
            elif recent.get("status") == 429:
                status = "paused_rate_limit"
            elif recent.get("status") == 0:
                status = "recent_results_refresh_error"
        if source == "livescore":
            catalog = state.get("live_catalog") or {}
            if catalog.get("status") == 200 and catalog.get("catalog_valid"):
                status = "live_match_discovery_catalog"
            elif catalog.get("status") == 429:
                status = "paused_rate_limit"
            elif catalog.get("status") is not None:
                status = "catalog_unavailable"
        if source == "live_tennis_api":
            status, extra_profile_history = _live_tennis_profile_status(state, now=now)
            if state.get("latest_live_catalog"):
                extra_profile_history = {
                    **extra_profile_history,
                    "catalog_observed_at": state["latest_live_catalog"].get("observed_at"),
                    "catalog_match_count": state["latest_live_catalog"].get("match_count", 0),
                }
        else:
            extra_profile_history = None
        if source == "api_tennis":
            api_jobs = job_counts.get(source, {})
            if now >= datetime(2026, 11, 7, tzinfo=timezone.utc):
                status = "cutoff_reached"
            elif api_jobs.get("queued", 0) and (quota or {}).get("daily_used", 0) >= 6000:
                status = "paused_daily_budget"
            elif api_jobs.get("running", 0):
                status = "archiving"
            elif api_jobs.get("queued", 0):
                status = "queued"
            elif api_jobs.get("complete", 0):
                status = "archive_complete"
        extra = {}
        extra["routing_enabled"] = {
            capability: _tennis_source_capability_enabled(source, capability)
            for capability in route_capabilities
        }
        extra["metrics"] = _summarize_tennis_source_metrics(state.get("request_metrics"), now)
        if source == "api_tennis":
            extra["offline_imports"] = _api_tennis_offline_import_status(state)
        if source == "livescore" and state.get("live_catalog"):
            catalog = state["live_catalog"]
            extra["live_catalog"] = {
                "observed_at": catalog.get("observed_at"),
                "match_count": catalog.get("match_count", 0),
                "identified_matches": catalog.get("identified_matches", 0),
                "tournaments": catalog.get("tournaments", []),
                "role": "discovery_only",
                "score_authority": "fonbet",
            }
        if source == "live_tennis_api":
            extra["profile_history"] = extra_profile_history
            if state.get("latest_live_catalog"):
                catalog = state["latest_live_catalog"]
                extra["live_catalog"] = {
                    "observed_at": catalog.get("observed_at"),
                    "match_count": catalog.get("match_count", 0),
                    "raw_object_key": catalog.get("raw_object_key"),
                    "role": "catalog_supplement_only",
                    "score_authority": "fonbet",
                }
        if source == "tennis_my_life":
            archive = state.get("historical_archive") or {}
            extra["historical_seasons"] = {
                "status": archive.get("status", "not_started"),
                "current_partition": archive.get("current_partition"),
                "completed_partitions": archive.get("next_partition", 0),
                "partitions_total": archive.get("partitions_total"),
                "through_year": archive.get("through_year"),
                "available_by_tour": archive.get("available_by_tour", {}),
                "unavailable_by_tour": archive.get("unavailable_by_tour", {}),
                "updated_at": archive.get("updated_at"),
                "start_year_by_tour": archive.get("start_year_by_tour", {}),
            }
        if source == "tennis_explorer" and state.get("recent_window"):
            recent = state["recent_window"]
            extra["recent_window"] = {
                "days_completed": recent.get("days_completed", []),
                "normalized_matches": recent.get("normalized_matches", 0),
                "coverage": recent.get("coverage"),
                "paused_until": recent.get("paused_until"),
                "canonical_projection": recent.get("canonical_projection")
                or state.get("canonical_projection"),
            }
            profile_import = state.get("profile_import") or {}
            profile_quota = state.get("profile_quota") or {}
            profile_states = [
                (key, value) for key, value in state.items()
                if key.startswith("profile:") and isinstance(value, dict)
            ]
            successful_profiles = [
                (key, value) for key, value in profile_states if value.get("status") == 200
            ]
            fresh_profiles = 0
            for _, value in successful_profiles:
                checked = value.get("checked_at") or value.get("observed_at")
                if not checked:
                    continue
                try:
                    checked_at = datetime.fromisoformat(checked.replace("Z", "+00:00"))
                    if timedelta(0) <= now - checked_at <= timedelta(days=7):
                        fresh_profiles += 1
                except (TypeError, ValueError):
                    continue
            latest_profile_check = max(
                (value.get("checked_at") or value.get("observed_at") or ""
                 for _, value in successful_profiles), default=None,
            )
            extra["profile_history"] = {
                "status": profile_import.get("status") or (
                    "accessible_snapshots_partial" if successful_profiles else "not_started"
                ),
                "attempted_today": profile_quota.get("daily_calls", 0),
                "daily_limit": _tennis_explorer_profile_daily_limit(profile_quota),
                "profiles_with_snapshot": len(successful_profiles),
                "fresh_snapshots": fresh_profiles,
                "latest_profile_check": latest_profile_check,
                "last_import": {
                    "attempted": profile_import.get("attempted", 0),
                    "normalized_matches": profile_import.get("imported", 0),
                    "projected_matches": profile_import.get("projected", 0),
                    "skipped_recent": profile_import.get("skipped", 0),
                    "complete_career_history": False,
                    "observed_at": profile_import.get("observed_at"),
                },
                "tours": metadata.get("profile_tours", []),
            }
        if source == "tennis_abstract":
            profile_import = state.get("profile_import") or {}
            profile_states = [value for key, value in state.items()
                              if key.startswith("profile:") and isinstance(value, dict)]
            extra["profile_history"] = {
                "status": profile_import.get("status") or "not_yet_imported",
                "attempted_requests": profile_import.get("attempted_requests", 0),
                "daily_limit": 10,
                "imported_match_observations": profile_import.get("imported_match_observations", 0),
                "recent_profiles": sum(value.get("status") == 200 for value in profile_states),
                "complete_career_history": False,
                "usable_for_metrics": any(value.get("usable_for_metrics") is True
                                           for value in profile_states),
                "projected_matches": sum(
                    int((value.get("canonical_projection") or {}).get("projected", 0) or 0)
                    + int((value.get("canonical_projection") or {}).get("merged", 0) or 0)
                    for value in profile_states
                ),
            }
        result.append({**metadata, "source": source, "status": status,
                       "updated_at": state.get("updated_at"), "quota": quota,
                       "jobs": job_counts.get(source, {}), "point_documents": point_counts.get(source, {}),
                       "player_coverage": player_coverage.get(source, []), **extra})
    return {"generated_at": now.isoformat(), "status": "ok", "sources": result}


@app.get("/api/events/{event_id}/flashscore-research")
def get_event_flashscore_research(event_id: uuid.UUID) -> Dict[str, Any]:
    from db.models import TennisMatchLookupJob, TennisPlayerRefreshJob, TennisPointRefreshJob
    from db.models.external_tennis import TennisPlayerProfile
    with SessionLocal() as session:
        event = session.get(Event, event_id)
        if event is None or event.sport_code != "tennis":
            raise HTTPException(status_code=404, detail="Tennis event not found")
        players = []
        for participant_id, name in ((event.participant_a_id, event.participant_a_name), (event.participant_b_id, event.participant_b_name)):
            profile = session.get(TennisPlayerProfile, participant_id) if participant_id else None
            job = session.get(TennisPlayerRefreshJob, participant_id) if participant_id else None
            players.append({"participant_id": str(participant_id) if participant_id else None, "name": name,
                "status": profile.sync_status if profile else "pending", "metrics_version": profile.metrics_version if profile else 0,
                "last_success_at": profile.last_success_at.isoformat() if profile and profile.last_success_at else None,
                "job_status": job.status if job else None, "reason": (job.last_error if job else None) or (profile.sync_error if profile else None)})
        point = session.get(TennisPointRefreshJob, event_id)
        match = session.get(TennisMatchLookupJob, event_id)
        return {"event_id": str(event_id), "mode": "live" if event.is_live else "prematch",
            "players": players, "point_history": {"status": point.status if point else "not_queued",
                "updated_at": point.updated_at.isoformat() if point else None,
                "next_retry_at": point.next_run_at.isoformat() if point else None,
                "reason": point.last_error if point else None},
            "match_lookup": {"status": match.status if match else "not_queued",
                "updated_at": match.updated_at.isoformat() if match else None,
                "next_retry_at": match.next_run_at.isoformat() if match else None,
                "source_match_id": match.source_match_id if match else None,
                "reason": match.last_error if match else None}}


@app.get("/api/scheduler/stats")
def get_scheduler_stats() -> Dict[str, Any]:
    """Returns scheduler metrics plus the persistent search queue counts."""
    from .research_queue import research_counts

    counts = research_counts()
    report: Dict[str, Any] = {}
    if global_candidate_scheduler:
        report = global_candidate_scheduler.get_status_report().model_dump()
    report.update(
        {
            "queue_length": counts["queued"] + counts["running"],
            "queued_count": counts["queued"],
            "running_count": counts["running"],
            "processed_total": counts["completed"],
        }
    )
    return report


@app.post("/api/research/queue/sync")
def sync_research_search_queue(limit: int = 3) -> Dict[str, Any]:
    """Puts live and prematch matches into the search queue and starts a few lookups."""
    from .research_queue import sync_search_queue

    return sync_search_queue(limit=limit)


@app.get("/api/events/{event_id}/research/status")
def get_research_status(event_id: str) -> Dict[str, str]:
    """queued, running, empty, or ready — without changing the evidence list contract."""
    from .research_queue import match_research_status

    return match_research_status(event_id)


@app.post("/api/scheduler/cooldowns/clear")
def clear_scheduler_cooldowns_and_cache() -> Dict[str, Any]:
    """Clears all active cooldowns and snapshot cache entries."""
    if not global_candidate_scheduler:
        return {"error": "Scheduler unavailable"}
    global_candidate_scheduler.cooldown_manager.clear()
    global_candidate_scheduler.snapshot_cache.clear()
    global_candidate_scheduler.priority_queue.clear()
    return {"status": "success", "message": "Cooldowns, snapshot cache, and queue cleared"}


class ModelRollbackRequest(BaseModel):
    sport_code: str = "tennis"
    target_version_tag: Optional[str] = None
    reason: Optional[str] = "Manual rollback via Model Control"


@app.post("/api/models/rollback")
def rollback_model_version(req: ModelRollbackRequest) -> Dict[str, Any]:
    """
    Rolls back the current ACTIVE model for a sport.
    Demotes the active model to RETIRED or STAGING and restores a target or previous version.
    """
    if not SessionLocal or not ModelVersion:
        raise HTTPException(status_code=503, detail="Database not available")

    with SessionLocal() as session:
        current_active = (
            session.query(ModelVersion)
            .filter(ModelVersion.sport_code == req.sport_code, ModelVersion.status == "ACTIVE")
            .order_by(desc(ModelVersion.created_at))
            .first()
        )

        if req.target_version_tag:
            target = (
                session.query(ModelVersion)
                .filter(ModelVersion.version_tag == req.target_version_tag, ModelVersion.sport_code == req.sport_code)
                .first()
            )
            if not target:
                raise HTTPException(status_code=404, detail=f"Target model version '{req.target_version_tag}' not found")
        else:
            target = (
                session.query(ModelVersion)
                .filter(
                    ModelVersion.sport_code == req.sport_code,
                    ModelVersion.status.in_(["RETIRED", "STAGING", "SHADOW"]),
                    ~ModelVersion.version_tag.startswith("test_"),
                )
                .order_by(desc(ModelVersion.created_at))
                .first()
            )
            if not target:
                raise HTTPException(status_code=400, detail=f"No previous model versions found to rollback to for {req.sport_code}")

        if str(target.version_tag or "").startswith("test_"):
            raise HTTPException(status_code=400, detail="A test version cannot become active")

        if current_active and current_active.id == target.id:
            raise HTTPException(status_code=400, detail="Target model is already the active model")

        demoted_tag = None
        if current_active:
            current_active.status = "RETIRED"
            demoted_tag = current_active.version_tag

        target.status = "ACTIVE"
        session.commit()

        return {
            "status": "rolled_back",
            "sport_code": req.sport_code,
            "demoted_version": demoted_tag,
            "restored_active_version": target.version_tag,
            "reason": req.reason,
            "metrics": target.metrics,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


class SimulateInferenceRequest(BaseModel):
    sport_code: str = "tennis"
    features: Dict[str, float]
    odds: Optional[Dict[str, float]] = None


@app.post("/api/models/simulate-inference")
def simulate_model_inference(req: SimulateInferenceRequest) -> Dict[str, Any]:
    """
    Simulates ML inference on a custom feature vector without placing a bet or altering ledger.
    Calculates estimated probabilities, implied odds, edges, and model confidence.
    """
    # Baseline estimation logic for sandbox testing
    features = req.features or {}
    elo_diff = features.get("elo_diff", features.get("rating_diff", 0.0))
    h2h_advantage = features.get("h2h_win_rate_diff", features.get("h2h_diff", 0.0))
    momentum = features.get("recent_form_diff", features.get("momentum_diff", 0.0))
    fatigue = features.get("fatigue_diff", 0.0)

    # Sigmoid score computation
    z = (elo_diff * 0.005) + (h2h_advantage * 1.2) + (momentum * 0.8) - (fatigue * 0.5)
    import math
    p1 = 1.0 / (1.0 + math.exp(-max(-10.0, min(10.0, z))))
    p2 = 1.0 - p1

    fair_odds_p1 = round(1.0 / max(0.01, p1), 2)
    fair_odds_p2 = round(1.0 / max(0.01, p2), 2)

    odds_eval = {}
    if req.odds:
        b_p1 = req.odds.get("outcome_a", req.odds.get("p1", 0.0))
        b_p2 = req.odds.get("outcome_b", req.odds.get("p2", 0.0))
        edge_p1 = round((p1 * b_p1) - 1.0, 4) if b_p1 > 1.0 else 0.0
        edge_p2 = round((p2 * b_p2) - 1.0, 4) if b_p2 > 1.0 else 0.0
        odds_eval = {
            "bookmaker_odds": {"outcome_a": b_p1, "outcome_b": b_p2},
            "edge": {"outcome_a": edge_p1, "outcome_b": edge_p2},
            "best_edge": max(edge_p1, edge_p2),
            "recommendation": "BET_OUTCOME_A" if edge_p1 > 0.03 and edge_p1 > edge_p2 else ("BET_OUTCOME_B" if edge_p2 > 0.03 else "PASS"),
        }

    return {
        "status": "success",
        "sport_code": req.sport_code,
        "mode": "simulation_sandbox",
        "predicted_probabilities": {
            "outcome_a": round(p1, 4),
            "outcome_b": round(p2, 4),
        },
        "fair_odds": {
            "outcome_a": fair_odds_p1,
            "outcome_b": fair_odds_p2,
        },
        "confidence": round(abs(p1 - 0.5) * 2.0, 4),
        "odds_evaluation": odds_eval,
        "input_features_count": len(features),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/system/status")
def get_system_cluster_status() -> Dict[str, Any]:
    """
    Comprehensive real-time status across all system components, models,
    database connection, queues, and bankroll.
    """
    now = datetime.now(timezone.utc).isoformat()
    db_ok = False
    active_models_summary = {}

    if SessionLocal and ModelVersion:
        try:
            with SessionLocal() as session:
                models = session.query(ModelVersion).filter(ModelVersion.status == "ACTIVE").all()
                for m in models:
                    active_models_summary[m.sport_code] = {
                        "version_tag": m.version_tag,
                        "model_name": m.model_name,
                        "market": m.market,
                        "metrics": m.metrics,
                    }
                db_ok = True
        except Exception:
            db_ok = False

    redis_ok = False
    try:
        import redis
        r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), socket_timeout=1.5)
        if r.ping():
            redis_ok = True
    except Exception:
        redis_ok = False

    bankroll_data = {}
    if BankrollService and SessionLocal:
        try:
            with SessionLocal() as session:
                acc = BankrollService.get_or_create_account(session, name=os.getenv("DEFAULT_ACCOUNT_NAME", "default_paper_account"))
                bankroll_data = {
                    "account_name": acc.name,
                    "currency": acc.currency,
                    "balance": float(acc.balance),
                    "total_equity": float(acc.total_equity),
                    "locked_exposure": float(acc.locked_exposure),
                }
        except Exception:
            pass

    scheduler_queue_len = 0
    if global_candidate_scheduler:
        scheduler_queue_len = global_candidate_scheduler.priority_queue.size()

    return {
        "status": "healthy" if db_ok and redis_ok else "degraded",
        "cluster_name": "neurobet-platform",
        "timestamp": now,
        "infrastructure": {
            "database": "online" if db_ok else "offline",
            "redis": "online" if redis_ok else "offline",
            "environment": os.getenv("APP_ENV", "development"),
            "bet_mode": os.getenv("BET_MODE", "SIMULATION"),
        },
        "active_models": active_models_summary,
        "bankroll": bankroll_data,
        "scheduler": {
            "queue_size": scheduler_queue_len,
            "status": "active" if global_candidate_scheduler else "inactive",
        },
        "supported_sports": ["tennis", "football", "basketball", "hockey", "volleyball"],
    }


# ============================================================================
# Observability / Prometheus Proxy Endpoints (Phase 20)
# ============================================================================

@app.get("/api/prometheus/query")
async def proxy_prometheus_query(query: str = Query(..., description="PromQL query expression")):
    """Proxy PromQL instant queries to internal Prometheus."""
    import urllib.parse
    import urllib.request
    prom_host = os.getenv("PROMETHEUS_INTERNAL_URL", "http://prometheus:9090")
    encoded_query = urllib.parse.urlencode({"query": query})
    url = f"{prom_host}/api/v1/query?{encoded_query}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content)
    except Exception as e:
        logger.error("Prometheus query proxy error: %s", e)
        raise HTTPException(status_code=502, detail=f"Prometheus proxy error: {str(e)}")


@app.get("/api/prometheus/targets")
async def proxy_prometheus_targets():
    """Proxy active scrape targets to internal Prometheus."""
    import urllib.request
    prom_host = os.getenv("PROMETHEUS_INTERNAL_URL", "http://prometheus:9090")
    url = f"{prom_host}/api/v1/targets"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content)
    except Exception as e:
        logger.error("Prometheus targets proxy error: %s", e)
        raise HTTPException(status_code=502, detail=f"Prometheus proxy error: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)

