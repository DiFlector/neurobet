from datetime import datetime
from typing import Dict, List, Literal, Optional
from pydantic import Field
from .base import BaseContract, utc_now


class Selection(BaseContract):
    """
    Specific betting outcome within a market.
    """
    selection_id: str = Field(..., description="Unique ID of the selection")
    outcome: str = Field(..., description="Canonical outcome code, e.g. player_a, player_b")
    name: str = Field(..., description="Human-readable label of the selection")
    odds: float = Field(..., gt=1.0, description="Decimal bookmaker odds offered (e.g., 1.55)")
    probability_implied: float = Field(..., gt=0.0, lt=1.0, description="Implied probability: 1 / odds")
    status: Literal["active", "suspended"] = Field(default="active", description="Selection availability")


class Market(BaseContract):
    """
    Betting market for an event.
    MVP for Tennis: match_winner.
    """
    market_id: str = Field(..., description="Unique ID of the market")
    market_type: str = Field(
        default="match_winner",
        description="Canonical market type, e.g. match_winner, set_winner, total_games",
    )
    name: str = Field(..., description="Market title as displayed by Fonbet")
    status: Literal["active", "suspended", "settled"] = Field(
        default="active",
        description="Market availability status",
    )
    selections: List[Selection] = Field(..., min_length=2, description="Available outcomes")


class OddsSnapshot(BaseContract):
    """
    Point-in-time snapshot of all active markets and odds for an event.
    """
    snapshot_id: str = Field(..., description="Unique identifier of this odds snapshot")
    event_id: str = Field(..., description="Canonical event ID")
    observed_at: datetime = Field(default_factory=utc_now, description="Exact timestamp observed by collector")
    source: str = Field(default="fonbet", description="Data source")
    markets: List[Market] = Field(default_factory=list, description="List of markets captured in this snapshot")
    content_hash: str = Field(..., description="SHA-256 hash of odds payload to avoid duplicate row insertion")
