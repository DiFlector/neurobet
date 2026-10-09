from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import Field
from .base import BaseContract


class TennisGameScore(BaseContract):
    """Tennis specific game state representation: points within current game."""
    points_a: str = Field(default="0", description="Points for Player A (0, 15, 30, 40, AD)")
    points_b: str = Field(default="0", description="Points for Player B (0, 15, 30, 40, AD)")
    is_tiebreak: bool = Field(default=False, description="Whether current game is a tiebreak")


class TennisSetScore(BaseContract):
    """Tennis score within a set."""
    set_number: int = Field(..., ge=1, le=5, description="Set number (1..5)")
    games_a: int = Field(default=0, ge=0, description="Games won by Player A")
    games_b: int = Field(default=0, ge=0, description="Games won by Player B")


class Event(BaseContract):
    """
    Canonical representation of a sporting event in Neurobet.
    """
    event_id: str = Field(..., description="Internal canonical event ID (UUID or urn)")
    source: str = Field(default="fonbet", description="Data provider source")
    source_event_id: str = Field(..., description="External source ID from Fonbet")
    sport_code: str = Field(default="tennis", description="Identifier of the sport (e.g., tennis)")
    tournament: str = Field(..., description="Tournament / League name (e.g., ATP, WTA, ITF)")
    participant_a: str = Field(..., description="Canonical name of Player A / Home")
    participant_b: str = Field(..., description="Canonical name of Player B / Away")
    scheduled_start: datetime = Field(..., description="Scheduled start time in UTC")
    is_live: bool = Field(default=False, description="True if match is currently live")
    status: Literal["prematch", "live", "finished", "interrupted", "cancelled"] = Field(
        default="prematch",
        description="Current event lifecycle status",
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata, e.g. surface")


class EventState(BaseContract):
    """
    Hierarchical snapshot of the live match state.
    For Tennis: match -> set -> game -> point.
    """
    event_id: str = Field(..., description="Canonical event ID")
    sport_code: str = Field(default="tennis", description="Sport code")
    current_period: int = Field(default=1, description="Current set number in tennis")
    sets: List[TennisSetScore] = Field(default_factory=list, description="Sets completed and current")
    current_game: TennisGameScore = Field(default_factory=TennisGameScore, description="Current game points")
    server: Optional[Literal["player_a", "player_b"]] = Field(
        default=None,
        description="Player currently serving",
    )
    is_break_point: bool = Field(default=False, description="True if receiving player has break point")
    match_clock_seconds: Optional[int] = Field(default=None, description="Match elapsed time in seconds")
    stats: Dict[str, Any] = Field(
        default_factory=dict,
        description="Sport-specific live stats (aces, double faults, 1st serve %, etc.)",
    )
