import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sports_core import registry
try:
    import tennis_adapter
except ImportError:
    pass

try:
    from data_quality import DataQualityEngine
    from db.connection import SessionLocal
except ImportError:
    DataQualityEngine = None
    SessionLocal = None

app = FastAPI(
    title="Neurobet API",
    version="1.0.0",
    description="Research & Betting Simulation API for Neurobet (Primary Sport: Tennis)",
    root_path=os.getenv("BACKEND_ROOT_PATH", ""),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: str
    app: str
    primary_sport: str
    server_time_utc: str
    timezone: str
    version: str


@app.get("/health", response_model=HealthResponse)
def health_check() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "app": "neurobet-backend",
        "primary_sport": os.getenv("PRIMARY_SPORT", "tennis"),
        "server_time_utc": datetime.now(timezone.utc).isoformat(),
        "timezone": "UTC",
        "version": "1.0.0",
    }


@app.get("/api/sports")
def get_supported_sports() -> List[Dict[str, Any]]:
    """Returns all active sports registered in the system."""
    primary = os.getenv("PRIMARY_SPORT", "tennis").lower()
    sports = []
    # If no sports dynamically registered yet, ensure tennis is present
    active_sports = registry.list_sports() or ["tennis"]
    for sport_code in active_sports:
        desc = registry.get_descriptor(sport_code)
        d = desc.model_dump()
        d["is_primary"] = (sport_code == primary)
        sports.append(d)
    return sports


@app.get("/api/sports/{sport_code}")
def get_sport_details(sport_code: str) -> Dict[str, Any]:
    """
    Returns sport descriptor. If sport is not implemented, returns
    status="UNSUPPORTED" and supported=False cleanly without error.
    """
    desc = registry.get_descriptor(sport_code)
    res = desc.model_dump()
    primary = os.getenv("PRIMARY_SPORT", "tennis").lower()
    res["is_primary"] = (sport_code.lower() == primary)
    return res


@app.get("/api/tennis/events")
def get_tennis_events() -> List[Dict[str, Any]]:
    """Placeholder endpoint for tennis events list."""
    return []


# Data Quality API endpoints
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


# Bankroll, Immutable Ledger & Decision Layer endpoints
try:
    from bankroll import (
        BankrollService, ReconciliationEngine, EventResultMatcher, BetSettlementEngine,
        CandidateSelector, CombinedDecisionPipeline, LLMStrategyComparator,
    )
    from contracts.decisions import DecisionPipelineConfig, CandidateItem, StrategyComparisonReport
    from sports_core import FonbetResultsParser
    from db.models.betting import LedgerEntry, Bet, BetSettlement, BetProposal
    from db.models.predictions import LLMDecision
except ImportError:
    BankrollService = None
    ReconciliationEngine = None
    EventResultMatcher = None
    BetSettlementEngine = None
    CandidateSelector = None
    CombinedDecisionPipeline = None
    LLMStrategyComparator = None
    DecisionPipelineConfig = None
    CandidateItem = None
    StrategyComparisonReport = None
    FonbetResultsParser = None
    LedgerEntry = None
    Bet = None
    BetSettlement = None
    BetProposal = None
    LLMDecision = None


@app.get("/api/bankroll")
def get_bankroll_summary() -> Dict[str, Any]:
    """Returns virtual bankroll summary (balance, exposure, equity, PnL)."""
    if not BankrollService or not SessionLocal:
        return {"error": "Bankroll service unavailable"}
    with SessionLocal() as session:
        account = BankrollService.get_or_create_account(session)
        summary = BankrollService.get_account_summary(session, account.id)
        session.commit()
        return summary


@app.get("/api/bankroll/ledger")
def get_ledger_entries(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """Returns immutable financial ledger transaction history."""
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


@app.get("/api/bankroll/bets")
def get_bets(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Returns list of paper bets (filter by PENDING, WON, LOST, VOID)."""
    if not SessionLocal or not Bet or not BankrollService:
        return []
    with SessionLocal() as session:
        account = BankrollService.get_or_create_account(session)
        q = session.query(Bet).filter(Bet.account_id == account.id)
        if status:
            q = q.filter(Bet.status == status.upper())
        bets = q.order_by(Bet.placed_at.desc()).limit(limit).all()
        return [
            {
                "id": str(b.id),
                "event_id": str(b.event_id),
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
# Settlement & Official Fonbet Results Endpoints (Phase 13)
# --------------------------------------------------------------------------
@app.post("/api/settlement/sync")
def sync_results_and_settle(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Sync match results from official Fonbet results feed (https://fon.bet/results),
    complete finished events in DB, and settle pending bets with strict idempotency.
    """
    if not SessionLocal or not FonbetResultsParser or not EventResultMatcher or not BetSettlementEngine:
        return {"error": "Settlement engine unavailable"}

    raw_data = payload or {}
    results_list = []

    # If raw_data provided directly, parse it
    if "events" in raw_data and "sections" in raw_data:
        results_list = FonbetResultsParser.parse_feed(raw_data)
    else:
        # Fetch from Fonbet results collector or endpoints
        import subprocess
        import json
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


# --------------------------------------------------------------------------
# Decision Layer (ML + LLM) Endpoints (Phase 16)
# --------------------------------------------------------------------------
@app.post("/api/decisions/evaluate")
def evaluate_decision_pipeline(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluates betting options through CandidateSelector and CombinedDecisionPipeline.
    Filters candidate options by statistical edge/confidence first to eliminate costs,
    persists qualitative LLM decisions to DB, and routes approved candidates as BetProposals.
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
        # Process non-shortlisted as filtered out
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
