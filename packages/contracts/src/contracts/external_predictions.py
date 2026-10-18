"""Contracts for external predictions and consensus aggregation."""

from datetime import datetime
from typing import List, Optional
from pydantic import Field
from .base import BaseContract, utc_now


class ExternalPredictionDTO(BaseContract):
    """Normalized external prediction from betting/expert portals."""

    id: Optional[str] = Field(default=None, description="UUID string if persisted")
    source_code: str = Field(..., description="Unique source code, e.g. stavka_tv, betzona, vseprosport")
    source_url: str = Field(..., description="Canonical source URL")
    sport_code: str = Field(default="tennis", description="Sport code, default tennis")
    tournament_name: str = Field(default="", description="Tournament or cup name")

    raw_participant_a: str = Field(..., description="Player / Team A as written on source")
    raw_participant_b: str = Field(..., description="Player / Team B as written on source")
    canonical_participant_a: str = Field(..., description="Normalized canonical name for player A")
    canonical_participant_b: str = Field(..., description="Normalized canonical name for player B")

    estimated_start_at: datetime = Field(..., description="Estimated match start timestamp in UTC")

    market_type: str = Field(default="match_winner", description="Standardized market type (match_winner, handicap_games, totals)")
    predicted_outcome: str = Field(..., description="Standardized selection (p1, p2, f1_plus, f2_minus, tb, tm)")
    raw_prediction_text: str = Field(..., description="Exact tip/prediction text on site")
    recommended_odds: Optional[float] = Field(default=None, gt=1.0, description="Recommended bookmaker odds")

    author_name: Optional[str] = Field(default=None, description="Expert or capper username")
    author_roi: Optional[float] = Field(default=None, description="Capper verified ROI percentage if present")
    author_winrate: Optional[float] = Field(default=None, description="Capper verified winrate percentage if present")
    reasoning_snippet: Optional[str] = Field(default=None, description="Extracted analysis snippet / text")

    status: str = Field(default="pending", description="Status: pending, matched, expired, settled")
    matched_event_id: Optional[str] = Field(default=None, description="UUID of matched Fonbet event")
    matched_at: Optional[datetime] = Field(default=None, description="Timestamp when matched to Fonbet event")

    created_at: datetime = Field(default_factory=utc_now, description="Record creation timestamp")
    expires_at: datetime = Field(..., description="Expiry timestamp (estimated_start_at + 24 hours)")


class PredictionConsensusSummary(BaseContract):
    """Consolidated market and expert consensus for a specific match event."""

    event_id: str = Field(..., description="Canonical Fonbet event ID")
    sport_code: str = Field(default="tennis", description="Sport code")
    total_predictions: int = Field(default=0, ge=0, description="Total number of external predictions collected")

    p1_count: int = Field(default=0, ge=0, description="Predictions favoring Player A")
    p2_count: int = Field(default=0, ge=0, description="Predictions favoring Player B")
    other_count: int = Field(default=0, ge=0, description="Predictions on totals or handicaps")

    p1_share: float = Field(default=0.0, ge=0.0, le=1.0, description="Fraction of predictions favoring Player A")
    p2_share: float = Field(default=0.0, ge=0.0, le=1.0, description="Fraction of predictions favoring Player B")

    avg_p1_odds: Optional[float] = Field(default=None, description="Average recommended odds for Player A")
    avg_p2_odds: Optional[float] = Field(default=None, description="Average recommended odds for Player B")

    consensus_pick: Optional[str] = Field(default=None, description="Predominant selection (e.g. p1, p2, or neutral)")
    consensus_confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence in consensus based on source count and agreement")

    sources: List[str] = Field(default_factory=list, description="Unique source portals contributing predictions")
    expert_snippets: List[str] = Field(default_factory=list, description="Top expert reasoning excerpts for LLM context")
    updated_at: datetime = Field(default_factory=utc_now, description="Calculation timestamp")
