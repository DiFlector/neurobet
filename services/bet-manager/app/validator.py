"""Bet Validator for bet-manager enforcing sport registry support and risk limits."""

from typing import Any, Dict, Optional
from pydantic import BaseModel
from contracts import BetProposal
from sports_core import registry


class BetValidationResult(BaseModel):
    is_valid: bool
    status: str  # "ACCEPTED" or "REJECTED"
    rejection_reason: Optional[str] = None
    risk_score: float = 0.0
    details: Dict[str, Any] = {}


class BetValidator:
    """Validates bet proposals against sport registry rules, bankroll limits, and risk policies."""

    def __init__(self, max_stake: float = 10000.0, min_odds: float = 1.05, max_odds: float = 50.0):
        self.max_stake = max_stake
        self.min_odds = min_odds
        self.max_odds = max_odds

    def validate_proposal(
        self,
        proposal: BetProposal | Dict[str, Any],
        current_bankroll: Optional[float] = None,
    ) -> BetValidationResult:
        """
        Comprehensive validation of a bet proposal.
        Enforces sport support check (rejection_reason='UNSUPPORTED_SPORT').
        """
        data = proposal.model_dump() if isinstance(proposal, BetProposal) else dict(proposal)
        sport_code = str(data.get("sport_code") or "").lower()

        # 1. Sport support check
        if not registry.is_supported(sport_code):
            return BetValidationResult(
                is_valid=False,
                status="REJECTED",
                rejection_reason="UNSUPPORTED_SPORT",
                risk_score=1.0,
                details={"sport_code": sport_code, "supported_sports": registry.list_sports()},
            )

        # 2. Sport-specific rules check
        is_sport_valid, sport_reason = registry.validate_proposal(data)
        if not is_sport_valid:
            return BetValidationResult(
                is_valid=False,
                status="REJECTED",
                rejection_reason=sport_reason or "SPORT_RULE_VIOLATION",
                risk_score=1.0,
                details={"sport_code": sport_code},
            )

        # 3. Odds boundaries
        odds = float(data.get("odds", 0.0))
        if odds < self.min_odds or odds > self.max_odds:
            return BetValidationResult(
                is_valid=False,
                status="REJECTED",
                rejection_reason="ODDS_OUT_OF_BOUNDS",
                risk_score=0.9,
                details={"odds": odds, "min": self.min_odds, "max": self.max_odds},
            )

        # 4. Stake limits
        stake = float(data.get("stake", 0.0))
        if stake <= 0:
            return BetValidationResult(
                is_valid=False,
                status="REJECTED",
                rejection_reason="INVALID_STAKE",
                risk_score=1.0,
                details={"stake": stake},
            )
        if stake > self.max_stake:
            return BetValidationResult(
                is_valid=False,
                status="REJECTED",
                rejection_reason="STAKE_EXCEEDS_LIMIT",
                risk_score=0.8,
                details={"stake": stake, "max_stake": self.max_stake},
            )

        # 5. Bankroll sufficiency check
        if current_bankroll is not None and stake > current_bankroll:
            return BetValidationResult(
                is_valid=False,
                status="REJECTED",
                rejection_reason="INSUFFICIENT_BANKROLL",
                risk_score=1.0,
                details={"stake": stake, "bankroll": current_bankroll},
            )

        return BetValidationResult(
            is_valid=True,
            status="ACCEPTED",
            rejection_reason=None,
            risk_score=0.1,
            details={"sport_code": sport_code, "stake": stake, "odds": odds},
        )
