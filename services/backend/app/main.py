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


# Bankroll & Immutable Ledger endpoints
try:
    from bankroll import BankrollService, ReconciliationEngine
    from db.models.betting import LedgerEntry, Bet
except ImportError:
    BankrollService = None
    ReconciliationEngine = None
    LedgerEntry = None
    Bet = None


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




if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
