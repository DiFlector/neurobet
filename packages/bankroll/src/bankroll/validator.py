"""Bet Validator for bet-manager enforcing sport registry support, shadow guards, and risk limits."""

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

    @property
    def rejection_details(self) -> str:
        return str(self.details.get("message", ""))


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
        Enforces sport support check and shadow guard.
        """
        data = proposal.model_dump() if isinstance(proposal, BetProposal) else dict(proposal)
        sport_code = str(data.get("sport_code") or "").lower()

        # 0. Shadow Mode Guard: Shadow models are strictly prohibited from creating virtual bets
        if data.get("is_shadow") is True or str(data.get("model_status", "")).upper() == "SHADOW":
            return BetValidationResult(
                is_valid=False,
                status="REJECTED",
                rejection_reason="SHADOW_MODEL_CANNOT_BET",
                risk_score=1.0,
                details={
                    "is_shadow": True,
                    "message": "Shadow models are strictly prohibited from creating virtual bets (cannot generate real or virtual bets)",
                },
            )

        # 1. Sport support check
        if not registry.is_supported(sport_code):
            return BetValidationResult(
                is_valid=False,
                status="REJECTED",
                rejection_reason="UNSUPPORTED_SPORT",
                risk_score=1.0,
                details={"sport_code": sport_code, "supported_sports": registry.list_sports()},
            )

        # 2. Odds range check
        odds = float(data.get("bookmaker_odds") or 0.0)
        if odds < self.min_odds or odds > self.max_odds:
            return BetValidationResult(
                is_valid=False,
                status="REJECTED",
                rejection_reason="ODDS_OUT_OF_RANGE",
                risk_score=1.0,
                details={"odds": odds, "min_odds": self.min_odds, "max_odds": self.max_odds},
            )

        # 3. Maximum stake limit check
        stake = float(data.get("suggested_stake") or data.get("recommended_stake") or 0.0)
        if stake <= 0.0:
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
                rejection_reason="STAKE_EXCEEDS_MAX_LIMIT",
                risk_score=1.0,
                details={"stake": stake, "max_stake": self.max_stake},
            )

        # 4. Bankroll sufficiency check
        if current_bankroll is not None and stake > current_bankroll:
            return BetValidationResult(
                is_valid=False,
                status="REJECTED",
                rejection_reason="INSUFFICIENT_BANKROLL",
                risk_score=1.0,
                details={"stake": stake, "current_bankroll": current_bankroll},
            )

        return BetValidationResult(
            is_valid=True,
            status="ACCEPTED",
            risk_score=round(min(1.0, stake / self.max_stake), 4),
            details={"stake": stake, "odds": odds, "sport_code": sport_code},
        )
