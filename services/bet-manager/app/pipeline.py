"""Comprehensive 20-point Bet Validation Pipeline enforcing data freshness and risk gates."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from db.models.events import Event
from db.models.odds import Market, MarketSelection, OddsSnapshot
from db.models.betting import VirtualAccount, Bet, LedgerEntry
from sports_core import registry
# Ensure tennis adapter is loaded
try:
    import tennis_adapter
except ImportError:
    pass

from .config import BetManagerConfig, config as default_config
from . import reject_codes


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ValidationReport:
    is_accepted: bool
    rejection_code: Optional[str] = None
    checks_passed: List[str] = field(default_factory=list)
    checks_failed: List[str] = field(default_factory=list)
    effective_odds: float = 0.0
    effective_edge: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


class BetValidationPipeline:
    """
    Evaluates bet proposals across 20 distinct technical and risk criteria.
    Guarantees ML/LLM predictions cannot directly create bets.
    """

    def __init__(self, config: Optional[BetManagerConfig] = None):
        self.config = config or default_config

    def validate(
        self,
        session: Session,
        account: VirtualAccount,
        proposal: Dict[str, Any],
        as_of: Optional[datetime] = None,
    ) -> ValidationReport:
        now = as_of or utc_now()
        passed: List[str] = []
        failed: List[str] = []
        details: Dict[str, Any] = {}

        # ---------------------------------------------------------------------
        # 1. Schema & Basic Field Types
        # ---------------------------------------------------------------------
        required_fields = [
            "proposal_id",
            "event_id",
            "market",
            "outcome",
            "selection_id",
            "bookmaker_odds",
            "model_probability",
            "edge",
            "suggested_stake",
        ]
        for f in required_fields:
            if f not in proposal or proposal[f] is None:
                failed.append("SCHEMA_VALIDATION")
                return ValidationReport(
                    is_accepted=False,
                    rejection_code=reject_codes.INVALID_SCHEMA,
                    checks_passed=passed,
                    checks_failed=failed,
                    details={"missing_field": f},
                )
        passed.append("SCHEMA_VALIDATION")

        try:
            event_id = uuid.UUID(str(proposal["event_id"]))
            selection_id = uuid.UUID(str(proposal["selection_id"]))
            stake = float(proposal["suggested_stake"])
            bookmaker_odds = float(proposal["bookmaker_odds"])
            model_probability = float(proposal["model_probability"])
            edge = float(proposal["edge"])
            sport_code = str(proposal.get("sport_code") or "tennis").lower()
            market_name = str(proposal["market"])
            outcome = str(proposal["outcome"])
        except (ValueError, TypeError) as ex:
            failed.append("SCHEMA_VALIDATION")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.INVALID_SCHEMA,
                checks_passed=passed,
                checks_failed=failed,
                details={"error": str(ex)},
            )

        # ---------------------------------------------------------------------
        # 2. Sport Support & Sport-Specific Rules
        # ---------------------------------------------------------------------
        if not registry.is_supported(sport_code):
            failed.append("SPORT_SUPPORT")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.UNSUPPORTED_SPORT,
                checks_passed=passed,
                checks_failed=failed,
                details={"sport_code": sport_code},
            )
        passed.append("SPORT_SUPPORT")

        sport_proposal_data = dict(proposal)
        sport_proposal_data.setdefault("selection", outcome)
        sport_proposal_data["sport_code"] = sport_code
        is_sport_valid, sport_reason = registry.validate_proposal(sport_proposal_data)
        if not is_sport_valid:
            failed.append("SPORT_RULES")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.SPORT_RULE_VIOLATION,
                checks_passed=passed,
                checks_failed=failed,
                details={"reason": sport_reason},
            )
        passed.append("SPORT_RULES")

        # ---------------------------------------------------------------------
        # 3. Event Existence, Active State, and Freshness
        # ---------------------------------------------------------------------
        event = session.get(Event, event_id)
        if not event:
            failed.append("EVENT_EXISTS")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.EVENT_NOT_FOUND,
                checks_passed=passed,
                checks_failed=failed,
                details={"event_id": str(event_id)},
            )
        passed.append("EVENT_EXISTS")

        if event.status in ("finished", "completed", "cancelled") or event.finished_at is not None:
            failed.append("EVENT_ACTIVE")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.EVENT_FINISHED,
                checks_passed=passed,
                checks_failed=failed,
                details={"status": event.status},
            )
        passed.append("EVENT_ACTIVE")

        # Check Event State Freshness
        event_last_seen = event.last_seen_at
        if event_last_seen.tzinfo is None:
            event_last_seen = event_last_seen.replace(tzinfo=timezone.utc)
        time_since_event_update = (now - event_last_seen).total_seconds()
        if time_since_event_update > self.config.max_state_staleness_sec:
            failed.append("EVENT_FRESHNESS")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.STALE_EVENT_STATE,
                checks_passed=passed,
                checks_failed=failed,
                details={
                    "last_seen_at": event_last_seen.isoformat(),
                    "age_seconds": round(time_since_event_update, 2),
                    "max_allowed": self.config.max_state_staleness_sec,
                },
            )
        passed.append("EVENT_FRESHNESS")

        # ---------------------------------------------------------------------
        # 4. Market & Selection Existence & Status
        # ---------------------------------------------------------------------
        market_stmt = select(Market).where(
            Market.event_id == event.id,
            (Market.market_type == market_name) | (Market.name == market_name),
        )
        market = session.execute(market_stmt).scalars().first()
        if not market:
            failed.append("MARKET_EXISTS")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.MARKET_NOT_FOUND,
                checks_passed=passed,
                checks_failed=failed,
                details={"market": market_name},
            )
        passed.append("MARKET_EXISTS")

        if market.status.lower() in ("suspended", "closed", "inactive"):
            failed.append("MARKET_ACTIVE")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.MARKET_SUSPENDED,
                checks_passed=passed,
                checks_failed=failed,
                details={"market_status": market.status},
            )
        passed.append("MARKET_ACTIVE")

        selection = session.get(MarketSelection, selection_id)
        if not selection or selection.market_id != market.id:
            # Fallback lookup by outcome if UUID didn't match directly
            sel_stmt = select(MarketSelection).where(
                MarketSelection.market_id == market.id,
                MarketSelection.outcome == outcome,
            )
            selection = session.execute(sel_stmt).scalars().first()

        if not selection:
            failed.append("SELECTION_EXISTS")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.SELECTION_NOT_FOUND,
                checks_passed=passed,
                checks_failed=failed,
                details={"selection_id": str(selection_id), "outcome": outcome},
            )
        passed.append("SELECTION_EXISTS")

        if selection.status.lower() in ("suspended", "closed", "inactive"):
            failed.append("SELECTION_ACTIVE")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.MARKET_SUSPENDED,
                checks_passed=passed,
                checks_failed=failed,
                details={"selection_status": selection.status},
            )
        passed.append("SELECTION_ACTIVE")

        # ---------------------------------------------------------------------
        # 5. Odds Freshness Check (<= 15 seconds)
        # ---------------------------------------------------------------------
        odds_stmt = (
            select(OddsSnapshot)
            .where(OddsSnapshot.selection_id == selection.id)
            .order_by(OddsSnapshot.observed_at.desc())
            .limit(1)
        )
        latest_odds = session.execute(odds_stmt).scalars().first()
        if not latest_odds:
            failed.append("ODDS_FRESHNESS")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.STALE_ODDS,
                checks_passed=passed,
                checks_failed=failed,
                details={"error": "no odds snapshots found"},
            )

        odds_obs_time = latest_odds.observed_at
        if odds_obs_time.tzinfo is None:
            odds_obs_time = odds_obs_time.replace(tzinfo=timezone.utc)
        time_since_odds = (now - odds_obs_time).total_seconds()
        if time_since_odds > self.config.max_odds_staleness_sec:
            failed.append("ODDS_FRESHNESS")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.STALE_ODDS,
                checks_passed=passed,
                checks_failed=failed,
                details={
                    "observed_at": odds_obs_time.isoformat(),
                    "age_seconds": round(time_since_odds, 2),
                    "max_allowed": self.config.max_odds_staleness_sec,
                },
            )
        passed.append("ODDS_FRESHNESS")

        # ---------------------------------------------------------------------
        # 6. Statistical Hurdles (Confidence & Edge)
        # ---------------------------------------------------------------------
        if model_probability < self.config.min_confidence:
            failed.append("CONFIDENCE_THRESHOLD")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.CONFIDENCE_TOO_LOW,
                checks_passed=passed,
                checks_failed=failed,
                details={"confidence": model_probability, "min": self.config.min_confidence},
            )
        passed.append("CONFIDENCE_THRESHOLD")

        if edge < self.config.min_edge:
            failed.append("EDGE_THRESHOLD")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.EDGE_TOO_LOW,
                checks_passed=passed,
                checks_failed=failed,
                details={"edge": edge, "min": self.config.min_edge},
            )
        passed.append("EDGE_THRESHOLD")

        # ---------------------------------------------------------------------
        # 7. Odds Movement / Slippage & Absolute Bounds
        # ---------------------------------------------------------------------
        current_odds = float(latest_odds.odds)
        if current_odds < self.config.min_odds or current_odds > self.config.max_odds:
            failed.append("ODDS_BOUNDS")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.PRICE_SLIPPED,
                checks_passed=passed,
                checks_failed=failed,
                effective_odds=current_odds,
                details={"current_odds": current_odds, "min": self.config.min_odds, "max": self.config.max_odds},
            )
        passed.append("ODDS_BOUNDS")

        # Dynamic slippage check: does effective edge with current bookmaker odds still satisfy min_edge?
        effective_edge = (model_probability * current_odds) - 1.0
        # Also check relative drop from proposed bookmaker_odds
        min_acceptable_odds = bookmaker_odds * (1.0 - self.config.allowed_slippage_pct)
        if effective_edge < self.config.min_edge or current_odds < min_acceptable_odds:
            failed.append("SLIPPAGE_EDGE")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.PRICE_SLIPPED,
                checks_passed=passed,
                checks_failed=failed,
                effective_odds=current_odds,
                effective_edge=effective_edge,
                details={
                    "bookmaker_odds": bookmaker_odds,
                    "current_odds": current_odds,
                    "effective_edge": round(effective_edge, 4),
                    "min_edge": self.config.min_edge,
                },
            )
        passed.append("SLIPPAGE_EDGE")

        # ---------------------------------------------------------------------
        # 8. Duplicate Bet Check
        # ---------------------------------------------------------------------
        dup_stmt = select(Bet).where(
            Bet.account_id == account.id,
            Bet.event_id == event.id,
            Bet.market == market_name,
            Bet.outcome == outcome,
            Bet.status == "PENDING",
        )
        existing_bet = session.execute(dup_stmt).scalars().first()
        if existing_bet:
            failed.append("DUPLICATE_CHECK")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.DUPLICATE_PROPOSAL,
                checks_passed=passed,
                checks_failed=failed,
                details={"existing_bet_id": str(existing_bet.id)},
            )
        passed.append("DUPLICATE_CHECK")

        # ---------------------------------------------------------------------
        # 9. Stake Limits & Constraints
        # ---------------------------------------------------------------------
        if stake < self.config.min_stake:
            failed.append("STAKE_LIMITS")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.STAKE_TOO_LOW,
                checks_passed=passed,
                checks_failed=failed,
                details={"stake": stake, "min": self.config.min_stake},
            )
        if stake > self.config.max_stake:
            failed.append("STAKE_LIMITS")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.STAKE_LIMIT_EXCEEDED,
                checks_passed=passed,
                checks_failed=failed,
                details={"stake": stake, "max": self.config.max_stake},
            )
        passed.append("STAKE_LIMITS")

        # ---------------------------------------------------------------------
        # 10. Bankroll Sufficiency
        # ---------------------------------------------------------------------
        available_balance = float(account.balance)
        if stake > available_balance:
            failed.append("BANKROLL_SUFFICIENCY")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.INSUFFICIENT_BANKROLL,
                checks_passed=passed,
                checks_failed=failed,
                details={"stake": stake, "available_balance": available_balance},
            )
        passed.append("BANKROLL_SUFFICIENCY")

        # ---------------------------------------------------------------------
        # 11. Portfolio Risk & Exposure Policy
        # ---------------------------------------------------------------------
        current_exposure = float(account.locked_exposure)
        max_allowed_exposure = float(account.initial_balance) * self.config.max_exposure_fraction
        if (current_exposure + stake) > max_allowed_exposure:
            failed.append("EXPOSURE_LIMIT")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.EXPOSURE_LIMIT,
                checks_passed=passed,
                checks_failed=failed,
                details={
                    "current_exposure": current_exposure,
                    "new_exposure": round(current_exposure + stake, 2),
                    "max_allowed": round(max_allowed_exposure, 2),
                },
            )
        passed.append("EXPOSURE_LIMIT")

        # ---------------------------------------------------------------------
        # 12. Daily Loss Limit Check
        # ---------------------------------------------------------------------
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        daily_loss_stmt = (
            select(func.coalesce(func.sum(LedgerEntry.amount), 0.0))
            .where(
                LedgerEntry.account_id == account.id,
                LedgerEntry.created_at >= today_start,
                LedgerEntry.amount < 0,
                LedgerEntry.entry_type == "BET_LOSS",
            )
        )
        today_losses = abs(float(session.execute(daily_loss_stmt).scalar() or 0.0))
        max_daily_loss = float(account.initial_balance) * self.config.daily_loss_limit_fraction
        if today_losses >= max_daily_loss:
            failed.append("DAILY_LOSS_LIMIT")
            return ValidationReport(
                is_accepted=False,
                rejection_code=reject_codes.DAILY_LOSS_LIMIT_EXCEEDED,
                checks_passed=passed,
                checks_failed=failed,
                details={
                    "today_losses": today_losses,
                    "max_allowed_loss": max_daily_loss,
                },
            )
        passed.append("DAILY_LOSS_LIMIT")

        # ---------------------------------------------------------------------
        # All checks passed!
        # ---------------------------------------------------------------------
        return ValidationReport(
            is_accepted=True,
            rejection_code=None,
            checks_passed=passed,
            checks_failed=[],
            effective_odds=current_odds,
            effective_edge=round(effective_edge, 4),
            details={
                "stake": stake,
                "odds": current_odds,
                "edge": round(effective_edge, 4),
                "market": market_name,
                "outcome": outcome,
            },
        )
