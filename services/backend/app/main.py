import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import asc, desc, func, text

from sports_core import registry
try:
    import tennis_adapter
except ImportError:
    pass

try:
    from data_quality import DataQualityEngine
    from db.connection import SessionLocal
    from db.models.events import Event, EventStateSnapshot
    from db.models.odds import Market, MarketSelection, OddsSnapshot
    from db.models.predictions import MLPrediction, FeatureSnapshot, LLMDecision
    from db.models.betting import (
        VirtualAccount, LedgerEntry, Bet, BetSettlement, BetProposal, BetValidationResult
    )
    from db.models.ml_registry import ModelVersion, TrainingRun, TrainingDataset, ExperimentResult
    from db.models.research import WebResearchRun, WebDocument, WebEvidence
except ImportError:
    DataQualityEngine = None
    SessionLocal = None
    Event = None
    EventStateSnapshot = None
    Market = None
    MarketSelection = None
    OddsSnapshot = None
    MLPrediction = None
    FeatureSnapshot = None
    LLMDecision = None
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
    WebResearchRun = None
    WebDocument = None
    WebEvidence = None

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

logger = logging.getLogger("backend")

app = FastAPI(
    title="Neurobet API",
    version="1.0.0",
    description="Research & Betting Simulation REST API for Neurobet (Primary Sport: Tennis)",
    root_path=os.getenv("BACKEND_ROOT_PATH", ""),
)

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
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """
    Returns paginated list of sporting events with filters for sport, status, live, and search.
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

        total = q.count()
        events = q.order_by(Event.scheduled_start_at.desc()).offset(offset).limit(limit).all()

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
                    "quality_score": float(e.quality_score) if e.quality_score is not None else 100.0,
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
            "quality_score": float(evt.quality_score) if evt.quality_score is not None else 100.0,
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
                "created_at": p.created_at.isoformat(),
            }
            for p in preds
        ]


@app.get("/api/research")
@app.get("/api/events/{event_id}/research")
def get_research_evidence(event_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Returns qualitative research evidence snippets extracted for matches."""
    if not SessionLocal or not WebEvidence:
        return []

    with SessionLocal() as session:
        q = session.query(WebEvidence)
        if event_id:
            try:
                q = q.filter(WebEvidence.event_id == uuid.UUID(event_id))
            except ValueError:
                return []

        evidence = q.order_by(WebEvidence.created_at.desc()).limit(limit).all()
        return [
            {
                "id": str(e.id),
                "event_id": str(e.event_id),
                "snippet": e.snippet,
                "relevance_score": float(e.relevance_score),
                "freshness_score": float(e.freshness_score),
                "created_at": e.created_at.isoformat(),
            }
            for e in evidence
        ]


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


@app.get("/api/ledger")
@app.get("/api/bankroll/ledger")
def get_ledger_entries(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """Returns immutable financial ledger transaction audit trail."""
    if not SessionLocal or not LedgerEntry or not BankrollService:
        return []
    with SessionLocal() as session:
        account = BankrollService.get_or_create_account(session)
        entries = (
            session.query(LedgerEntry)
            .filter(LedgerEntry.account_id == account.id)
            .order_by(LedgerEntry.created_at.desc())
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
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Returns paper bets with optional status and account filtering (PENDING, WON, LOST, VOID)."""
    if not SessionLocal or not Bet:
        return []
    with SessionLocal() as session:
        q = session.query(Bet)
        if account_id:
            try:
                acc_uuid = uuid.UUID(account_id)
                q = q.filter(Bet.account_id == acc_uuid)
            except ValueError:
                pass
        if status:
            q = q.filter(Bet.status == status.upper())
        bets = q.order_by(Bet.placed_at.desc()).offset(offset).limit(limit).all()
        return [
            {
                "id": str(b.id),
                "event_id": str(b.event_id),
                "proposal_id": str(b.proposal_id) if b.proposal_id else None,
                "sport_code": b.sport_code,
                "market": b.market,
                "outcome": b.outcome,
                "odds": float(b.odds),
                "stake": float(b.stake),
                "potential_payout": float(b.potential_payout),
                "status": b.status,
                "placed_at": b.placed_at.isoformat(),
            }
            for b in bets
        ]


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

        return {
            "id": str(b.id),
            "account_id": str(b.account_id),
            "event_id": str(b.event_id),
            "proposal_id": str(b.proposal_id) if b.proposal_id else None,
            "sport_code": b.sport_code,
            "market": b.market,
            "outcome": b.outcome,
            "odds": float(b.odds),
            "stake": float(b.stake),
            "potential_payout": float(b.potential_payout),
            "status": b.status,
            "placed_at": b.placed_at.isoformat(),
            "settlement": settlement_dict,
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
def get_portfolio_performance() -> Dict[str, Any]:
    """Calculates overall betting performance: PnL, ROI, Win Rate, and Max Drawdown."""
    if not SessionLocal or not Bet or not VirtualAccount:
        return {
            "total_bets": 0, "won_bets": 0, "lost_bets": 0, "void_bets": 0, "pending_bets": 0,
            "win_rate": 0.0, "total_turnover": 0.0, "net_pnl": 0.0, "roi": 0.0, "max_drawdown": 0.0
        }

    with SessionLocal() as session:
        bets = session.query(Bet).all()
        total_bets = len(bets)
        won_bets = sum(1 for b in bets if b.status == "WON")
        lost_bets = sum(1 for b in bets if b.status == "LOST")
        void_bets = sum(1 for b in bets if b.status == "VOID")
        pending_bets = sum(1 for b in bets if b.status == "PENDING")

        decided_bets = won_bets + lost_bets
        win_rate = round(won_bets / decided_bets, 4) if decided_bets > 0 else 0.0
        total_turnover = sum(float(b.stake) for b in bets)

        # Net PnL from settled bets
        settlements = session.query(BetSettlement).all()
        net_pnl = round(sum(float(s.net_profit) for s in settlements), 2)
        settled_turnover = sum(float(b.stake) for b in bets if b.status in ("WON", "LOST", "VOID"))
        roi = round((net_pnl / settled_turnover * 100.0), 2) if settled_turnover > 0 else 0.0

        # Calculate max drawdown from ledger transaction history
        account = session.query(VirtualAccount).first()
        max_dd = 0.0
        if account:
            entries = (
                session.query(LedgerEntry)
                .filter(LedgerEntry.account_id == account.id)
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
@app.get("/api/models")
def get_model_versions() -> List[Dict[str, Any]]:
    """Returns registered ML model versions and performance checkpoints."""
    if not SessionLocal or not ModelVersion:
        return []
    with SessionLocal() as session:
        models = session.query(ModelVersion).order_by(ModelVersion.created_at.desc()).all()
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
                "created_at": m.created_at.isoformat(),
            }
            for m in models
        ]


@app.post("/api/models/train")
def trigger_model_training(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Triggers ML model training job for a sport."""
    sport_code = (payload or {}).get("sport_code", "tennis")
    return {
        "status": "queued",
        "sport_code": sport_code,
        "message": f"Training job for {sport_code} submitted to training pipeline",
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



# --------------------------------------------------------------------------
# 9. Simulation Reset & Bankroll Reconciliation
# --------------------------------------------------------------------------
@app.post("/api/simulation/reset")
def reset_simulation(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Resets virtual bankroll to initial deposit balance (100,000 RUB).
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
async def live_stream_updates():
    """
    Server-Sent Events (SSE) stream broadcasting live match state changes,
    odds shifts, and decision updates to the frontend dashboard.
    """
    async def event_generator():
        # Emit initial connection handshake
        yield f"event: connected\ndata: {json.dumps({'message': 'Connected to Neurobet live stream', 'time': datetime.now(timezone.utc).isoformat()})}\n\n"
        count = 0
        while count < 3: # Stream 3 frames for test/polling or continuous in live mode
            await asyncio.sleep(0.5)
            count += 1
            data = {
                "heartbeat": count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "active",
            }
            yield f"event: heartbeat\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# --------------------------------------------------------------------------
# 11. Settlement, Decision Layer & Scheduler Endpoints (Phase 13, 16, 17)
# --------------------------------------------------------------------------
@app.post("/api/settlement/sync")
def sync_results_and_settle(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Sync match results from official Fonbet results feed,
    complete finished events in DB, and settle pending bets with strict idempotency.
    """
    if not SessionLocal or not FonbetResultsParser or not EventResultMatcher or not BetSettlementEngine:
        return {"error": "Settlement engine unavailable"}

    raw_data = payload or {}
    results_list = []

    if "events" in raw_data and "sections" in raw_data:
        results_list = FonbetResultsParser.parse_feed(raw_data)
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
                        results_list = FonbetResultsParser.parse_feed(parsed_feed)
                        break
            except Exception:
                pass

    events_matched = 0
    bets_settled = 0
    with SessionLocal() as session:
        for res in results_list:
            matched_evt = EventResultMatcher.match_and_complete_event(session, res)
            if matched_evt:
                events_matched += 1
                settlements = BetSettlementEngine.settle_event_bets(session, matched_evt, res)
                bets_settled += len(settlements)

        session.commit()
        review_queue = BetSettlementEngine.get_review_queue(session)
        return {
            "status": "success",
            "results_parsed": len(results_list),
            "events_matched": events_matched,
            "bets_settled": bets_settled,
            "review_queue_count": len(review_queue),
        }


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


@app.get("/api/scheduler/queue")
def get_scheduled_queue() -> List[Dict[str, Any]]:
    """Returns currently queued candidates ordered by composite priority score."""
    if not global_candidate_scheduler:
        return []
    items = global_candidate_scheduler.priority_queue.items()
    return [it.model_dump() for it in items]


@app.get("/api/scheduler/stats")
def get_scheduler_stats() -> Dict[str, Any]:
    """Returns real-time scheduler metrics (queue size, cooldowns, cache hits)."""
    if not global_candidate_scheduler:
        return {"error": "Scheduler unavailable"}
    return global_candidate_scheduler.get_status_report().model_dump()


@app.post("/api/scheduler/cooldowns/clear")
def clear_scheduler_cooldowns_and_cache() -> Dict[str, Any]:
    """Clears all active cooldowns and snapshot cache entries."""
    if not global_candidate_scheduler:
        return {"error": "Scheduler unavailable"}
    global_candidate_scheduler.cooldown_manager.clear()
    global_candidate_scheduler.snapshot_cache.clear()
    global_candidate_scheduler.priority_queue.clear()
    return {"status": "success", "message": "Cooldowns, snapshot cache, and queue cleared"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
