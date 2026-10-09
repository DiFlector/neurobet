"""FastAPI application and HTTP API for Bet Manager service."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os
from typing import Any, Dict, List, Optional
import uuid

from fastapi import FastAPI, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.orm import Session
import uvicorn

from db.connection import get_db, SessionLocal
from db.models.betting import (
    BetProposal as DBBetProposal,
    BetValidationResult as DBBetValidationResult,
    VirtualAccount,
    Bet,
)
from bankroll.service import BankrollService
from sports_core import registry
try:
    import tennis_adapter
except ImportError:
    pass

from .config import config
from .executor import SimulationExecutor, ExecutionResult

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s UTC [%(levelname)s] [bet-manager] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bet-manager")

executor = SimulationExecutor(config=config)


async def heartbeat_loop():
    """Maintain /tmp/healthy for Docker container health check."""
    while True:
        try:
            with open("/tmp/healthy", "w") as f:
                f.write(datetime.now(timezone.utc).isoformat())
        except Exception as e:
            logger.error("Failed to update heartbeat file: %s", e)
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    bet_mode = os.getenv("BET_MODE", "SIMULATION").upper()
    if bet_mode != "SIMULATION":
        logger.critical(
            "FATAL SECURITY VIOLATION: Unauthorized BET_MODE='%s'. Only 'SIMULATION' mode is permitted.",
            bet_mode,
        )
        raise RuntimeError(
            f"FATAL SECURITY VIOLATION: BET_MODE={bet_mode} is forbidden. System operates strictly in SIMULATION mode."
        )

    logger.info("Starting Bet-Manager service in mode: %s", bet_mode)
    logger.info("Supported sports: %s", registry.list_sports())

    # Ensure default virtual account is initialized
    with SessionLocal() as session:
        try:
            account = BankrollService.get_or_create_account(
                session=session,
                name=config.default_account_name,
                initial_balance=config.initial_bankroll,
                currency=config.currency,
            )
            session.commit()
            logger.info("Initialized default paper account: %s (Balance: %.2f)", account.name, account.balance)
        except Exception as e:
            session.rollback()
            logger.warning("Could not pre-initialize virtual account on startup: %s", e)

    heartbeat_task = asyncio.create_task(heartbeat_loop())
    yield
    # Shutdown
    heartbeat_task.cancel()
    logger.info("Bet-Manager service shut down cleanly.")


app = FastAPI(
    title="Neurobet Bet Manager API",
    description="Isolated gatekeeper service managing bet validation, risk policies, and paper bankroll.",
    version="1.0.0",
    lifespan=lifespan,
)

try:
    from observability import setup_observability
    setup_observability(app, service_name="bet-manager")
except ImportError:
    pass


class ProposalRequest(BaseModel):
    proposal_id: Optional[str] = None
    event_id: str
    sport_code: str = "tennis"
    market: str = "match_winner"
    outcome: str
    selection_id: str
    bookmaker_odds: float
    fair_odds: float
    model_probability: float
    edge: float
    suggested_stake: float
    ml_prediction_id: Optional[str] = None
    llm_decision_id: Optional[str] = None


@app.get("/health")
def healthcheck():
    return {
        "status": "healthy",
        "service": "bet-manager",
        "mode": os.getenv("BET_MODE", "SIMULATION"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "supported_sports": registry.list_sports(),
    }


@app.post("/api/proposals/evaluate", response_model=Dict[str, Any])
def evaluate_proposal(
    request: ProposalRequest,
    account_name: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Evaluate a bet proposal against all 20 risk and data freshness gates.
    If accepted, atomically places the bet in virtual bankroll.
    """
    payload = request.model_dump()
    if not payload.get("proposal_id"):
        payload["proposal_id"] = str(uuid.uuid4())

    result = executor.process_proposal(
        session=db,
        proposal_dict=payload,
        account_name=account_name,
    )

    return {
        "proposal_id": result.proposal_id,
        "is_accepted": result.is_accepted,
        "rejection_code": result.rejection_code,
        "bet_id": result.bet_id,
        "checks_passed": result.checks_passed,
        "checks_failed": result.checks_failed,
        "effective_odds": result.effective_odds,
        "stake": result.stake,
        "details": result.details,
    }


@app.get("/api/proposals", response_model=List[Dict[str, Any]])
def list_proposals(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List recent bet proposals."""
    stmt = (
        select(DBBetProposal)
        .order_by(desc(DBBetProposal.created_at))
        .offset(offset)
        .limit(limit)
    )
    proposals = db.execute(stmt).scalars().all()
    return [
        {
            "id": str(p.id),
            "event_id": str(p.event_id),
            "sport_code": p.sport_code,
            "market": p.market,
            "outcome": p.outcome,
            "selection_id": str(p.selection_id),
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


@app.get("/api/proposals/{proposal_id}/validation", response_model=Dict[str, Any])
def get_proposal_validation(
    proposal_id: str,
    db: Session = Depends(get_db),
):
    """Retrieve full audit validation result for a given proposal."""
    try:
        prop_uuid = uuid.UUID(proposal_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid proposal_id format")

    stmt = select(DBBetValidationResult).where(DBBetValidationResult.proposal_id == prop_uuid)
    result = db.execute(stmt).scalars().first()
    if not result:
        raise HTTPException(status_code=404, detail="Validation result not found for proposal")

    return {
        "id": str(result.id),
        "proposal_id": str(result.proposal_id),
        "is_accepted": result.is_accepted,
        "rejection_code": result.rejection_code,
        "checks_passed": result.checks_passed,
        "checks_failed": result.checks_failed,
        "created_at": result.created_at.isoformat(),
    }


@app.get("/api/bankroll", response_model=Dict[str, Any])
def get_bankroll_summary(
    account_name: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Retrieve account summary, exposure, and balance audit status."""
    acc_name = account_name or config.default_account_name
    account = BankrollService.get_or_create_account(
        session=db,
        name=acc_name,
        initial_balance=config.initial_bankroll,
        currency=config.currency,
    )
    return BankrollService.get_account_summary(session=db, account_id=account.id)


def start():
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    start()
