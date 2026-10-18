"""Simulation Executor registering bet proposals, validation audit records, and virtual bets."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models.events import Event
from db.models.betting import (
    BetProposal as DBBetProposal,
    BetValidationResult as DBBetValidationResult,
    VirtualAccount,
    Bet,
)
from bankroll.service import BankrollService

from .config import BetManagerConfig, config as default_config
from .pipeline import BetValidationPipeline, ValidationReport
from . import reject_codes

logger = logging.getLogger("bet-manager.executor")


@dataclass
class ExecutionResult:
    proposal_id: str
    is_accepted: bool
    rejection_code: Optional[str] = None
    bet_id: Optional[str] = None
    checks_passed: List[str] = field(default_factory=list)
    checks_failed: List[str] = field(default_factory=list)
    effective_odds: float = 0.0
    stake: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


class SimulationExecutor:
    """
    Executes bet decision pipeline in simulation mode:
    1. Validates proposal across all gates.
    2. Persists proposal and validation audit records.
    3. Atomically books accepted bet in virtual bankroll ledger via BankrollService.
    """

    def __init__(self, config: Optional[BetManagerConfig] = None):
        self.config = config or default_config
        self.pipeline = BetValidationPipeline(config=self.config)

    def process_proposal(
        self,
        session: Session,
        proposal_dict: Dict[str, Any],
        account_name: Optional[str] = None,
        as_of: Optional[datetime] = None,
    ) -> ExecutionResult:
        acc_name = account_name or self.config.default_account_name

        # Ensure virtual account exists
        account = BankrollService.get_or_create_account(
            session=session,
            name=acc_name,
            initial_balance=self.config.initial_bankroll,
            currency=self.config.currency,
        )

        # 1. Run validation pipeline
        report: ValidationReport = self.pipeline.validate(
            session=session,
            account=account,
            proposal=proposal_dict,
            as_of=as_of,
        )

        if report.rejection_code == reject_codes.DUPLICATE_PROPOSAL:
            existing = session.execute(
                select(Bet).where(
                    Bet.event_id == uuid.UUID(str(proposal_dict["event_id"])),
                    Bet.market == str(proposal_dict.get("market") or ""),
                    Bet.outcome == str(proposal_dict.get("outcome") or ""),
                    Bet.status == "PENDING",
                )
            ).scalars().first()
            if existing is not None and str(existing.proposal_id) == str(proposal_dict.get("proposal_id") or ""):
                session.rollback()
                return ExecutionResult(
                    proposal_id=str(proposal_dict.get("proposal_id") or existing.proposal_id),
                    is_accepted=True,
                    bet_id=str(existing.id),
                    checks_passed=report.checks_passed,
                    effective_odds=float(existing.odds),
                    stake=float(existing.stake),
                    details={"idempotent": True},
                )

        proposal_id_str = str(proposal_dict.get("proposal_id") or uuid.uuid4())
        try:
            prop_uuid = uuid.UUID(proposal_id_str)
        except ValueError:
            prop_uuid = uuid.uuid4()
            proposal_id_str = str(prop_uuid)

        # 2. Persist proposal record in DB
        db_prop = session.get(DBBetProposal, prop_uuid)
        if not db_prop:
            ml_pred_id = None
            if proposal_dict.get("ml_prediction_id"):
                try:
                    cand_ml = uuid.UUID(str(proposal_dict["ml_prediction_id"]))
                    from db.models.predictions import MLPrediction
                    ml_pred_record = session.get(MLPrediction, cand_ml)
                    if ml_pred_record and not bool(ml_pred_record.is_shadow):
                        ml_pred_id = cand_ml
                except Exception:
                    ml_pred_id = None

            llm_dec_id = None
            if proposal_dict.get("llm_decision_id"):
                try:
                    cand_llm = uuid.UUID(str(proposal_dict["llm_decision_id"]))
                    from db.models.predictions import LLMDecision
                    if session.get(LLMDecision, cand_llm):
                        llm_dec_id = cand_llm
                except Exception:
                    llm_dec_id = None

            try:
                event_uuid = uuid.UUID(str(proposal_dict["event_id"]))
                sel_uuid = uuid.UUID(str(proposal_dict["selection_id"]))
            except (KeyError, ValueError):
                # Cannot persist BetProposal without valid event/selection UUID
                event_uuid = None
                sel_uuid = None

            event_obj = session.get(Event, event_uuid) if event_uuid else None
            if event_obj and sel_uuid:
                db_prop = DBBetProposal(
                    id=prop_uuid,
                    event_id=event_uuid,
                    sport_code=str(proposal_dict.get("sport_code") or "tennis"),
                    market=str(proposal_dict.get("market") or "match_winner"),
                    outcome=str(proposal_dict.get("outcome") or ""),
                    selection_id=sel_uuid,
                    bookmaker_odds=float(proposal_dict.get("bookmaker_odds") or 1.0),
                    fair_odds=float(proposal_dict.get("fair_odds") or 1.0),
                    model_probability=float(proposal_dict.get("model_probability") or 0.0),
                    edge=float(proposal_dict.get("edge") or 0.0),
                    suggested_stake=float(proposal_dict.get("suggested_stake") or 0.0),
                    ml_prediction_id=ml_pred_id,
                    llm_decision_id=llm_dec_id,
                )
                session.add(db_prop)
                try:
                    session.flush()
                except Exception:
                    session.rollback()
                    db_prop = session.get(DBBetProposal, prop_uuid)
                    if db_prop is None:
                        raise

        # 3. Persist audit validation result
        if db_prop:
            audit = dict(report.details or {})
            audit.setdefault("expected_value", report.effective_edge)
            audit.setdefault("max_loss", float(proposal_dict.get("suggested_stake") or 0.0))
            if report.effective_odds and audit["max_loss"]:
                audit.setdefault("potential_win", round(audit["max_loss"] * (report.effective_odds - 1.0), 2))
            db_val = DBBetValidationResult(
                proposal_id=db_prop.id,
                is_accepted=report.is_accepted,
                rejection_code=report.rejection_code,
                checks_passed=report.checks_passed,
                checks_failed=report.checks_failed,
                details=audit,
            )
            session.add(db_val)
            session.flush()

        # 4. If accepted, atomically place virtual bet via BankrollService
        placed_bet_id: Optional[str] = None
        if report.is_accepted and db_prop:
            existing = session.execute(
                select(Bet).where(Bet.proposal_id == db_prop.id)
            ).scalars().first()
            if existing is not None:
                session.commit()
                return ExecutionResult(
                    proposal_id=proposal_id_str,
                    is_accepted=True,
                    bet_id=str(existing.id),
                    checks_passed=report.checks_passed,
                    effective_odds=float(existing.odds),
                    stake=float(existing.stake),
                    details=report.details,
                )
            bet = BankrollService.place_bet(
                session=session,
                account_id=account.id,
                proposal_id=db_prop.id,
                event_id=db_prop.event_id,
                sport_code=db_prop.sport_code,
                market=db_prop.market,
                outcome=db_prop.outcome,
                odds=report.effective_odds,
                stake=float(proposal_dict["suggested_stake"]),
            )
            placed_bet_id = str(bet.id)
            logger.info(
                f"Simulated bet placed: id={bet.id}, account={account.name}, "
                f"market={bet.market}, outcome={bet.outcome}, odds={bet.odds}, stake={bet.stake}"
            )

        session.commit()

        return ExecutionResult(
            proposal_id=proposal_id_str,
            is_accepted=report.is_accepted,
            rejection_code=report.rejection_code,
            bet_id=placed_bet_id,
            checks_passed=report.checks_passed,
            checks_failed=report.checks_failed,
            effective_odds=report.effective_odds,
            stake=float(proposal_dict.get("suggested_stake") or 0.0),
            details=report.details,
        )
