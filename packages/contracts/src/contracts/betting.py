from datetime import datetime
from typing import List, Literal, Optional
from pydantic import Field
from .base import BaseContract, utc_now


class BetProposal(BaseContract):
    """
    Candidate bet recommended by the ML/LLM pipeline to bet-manager.
    """
    proposal_id: str = Field(..., description="Unique proposal identifier")
    event_id: str = Field(..., description="Canonical event ID")
    sport_code: str = Field(default="tennis", description="Sport code")
    market: str = Field(default="match_winner", description="Market name")
    outcome: str = Field(..., description="Target outcome (e.g. player_a)")
    selection_id: str = Field(..., description="Target selection ID in current odds snapshot")
    bookmaker_odds: float = Field(..., gt=1.0, description="Bookmaker odds at moment of proposal")
    fair_odds: float = Field(..., gt=1.0, description="Fair model odds")
    model_probability: float = Field(..., ge=0.0, le=1.0, description="Model probability")
    edge: float = Field(..., description="Calculated edge")
    suggested_stake: float = Field(..., gt=0.0, description="Recommended stake amount")
    ml_prediction_id: str = Field(..., description="Linked ML prediction ID")
    llm_decision_id: Optional[str] = Field(default=None, description="Optional linked LLM verdict ID")


class BetValidationResult(BaseContract):
    """
    Technical decision made by bet-manager verifying safety rules and bankroll.
    """
    proposal_id: str = Field(..., description="Linked proposal ID")
    is_accepted: bool = Field(..., description="True if proposal passed all validation gates")
    rejection_code: Optional[str] = Field(
        default=None,
        description="Error code if rejected (e.g. STALE_ODDS, INSUFFICIENT_BANKROLL, EXPOSURE_LIMIT)",
    )
    checks_passed: List[str] = Field(default_factory=list, description="List of passed validation checks")
    checks_failed: List[str] = Field(default_factory=list, description="List of failed validation checks")


class VirtualBet(BaseContract):
    """
    Accepted simulated bet registered in virtual bankroll ledger.
    """
    bet_id: str = Field(..., description="Unique bet identifier")
    proposal_id: str = Field(..., description="Linked proposal ID")
    event_id: str = Field(..., description="Canonical event ID")
    sport_code: str = Field(default="tennis", description="Sport code")
    market: str = Field(..., description="Market name")
    outcome: str = Field(..., description="Target outcome")
    odds: float = Field(..., gt=1.0, description="Locked decimal odds")
    stake: float = Field(..., gt=0.0, description="Simulated money staked")
    potential_payout: float = Field(..., gt=0.0, description="stake * odds")
    placed_at: datetime = Field(default_factory=utc_now, description="Timestamp placed in UTC")
    status: Literal["PENDING", "WON", "LOST", "VOID"] = Field(
        default="PENDING",
        description="Current lifecycle state",
    )


class Settlement(BaseContract):
    """
    Settlement record generated after match conclusion.
    """
    bet_id: str = Field(..., description="Linked virtual bet ID")
    status: Literal["WON", "LOST", "VOID"] = Field(..., description="Final settlement status")
    payout: float = Field(default=0.0, ge=0.0, description="Actual payout credited (0.0 if lost)")
    net_profit: float = Field(..., description="payout - stake")
    settled_at: datetime = Field(default_factory=utc_now, description="Settlement time in UTC")
    settlement_reason: str = Field(default="MATCH_COMPLETED", description="Rule or event outcome description")


class CanonicalMatchResult(BaseContract):
    """
    Official match result parsed from bookmaker results service (e.g. fon.bet/results).
    Guarantees no match result is guessed by heuristics.
    """
    source_event_id: str = Field(..., description="Bookmaker event identifier")
    sport_code: str = Field(default="tennis", description="Sport code")
    tournament: str = Field(..., description="Tournament / competition name")
    participant_a: str = Field(..., description="Participant / Team 1 name")
    participant_b: str = Field(..., description="Participant / Team 2 name")
    final_score: str = Field(..., description="Official final score string, e.g. '2:1 (6-4 3-6 6-1)'")
    winner: Optional[Literal["player_a", "player_b"]] = Field(
        default=None,
        description="Winning participant if match completed cleanly, None if cancelled or void",
    )
    status: Literal["FINISHED", "CANCELLED", "RETIRED", "VOID", "SETTLEMENT_REVIEW_REQUIRED"] = Field(
        default="FINISHED",
        description="Canonical outcome status",
    )
    is_retired: bool = Field(default=False, description="True if one participant retired/withdrew")
    is_walkover: bool = Field(default=False, description="True if match was a walkover")
    comments: str = Field(default="", description="Official comments/reasons from bookmaker results")
