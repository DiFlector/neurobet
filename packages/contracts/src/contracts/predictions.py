from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import Field
from .base import BaseContract, utc_now


class FeatureVector(BaseContract):
    """
    Normalized numerical feature vector prepared for ML inference.
    Strictly free from data leakage (only historical / at-the-moment features).
    """
    event_id: str = Field(..., description="Canonical event ID")
    sport_code: str = Field(default="tennis", description="Sport code")
    feature_version: str = Field(default="1.0.0", description="Version of feature engineering pipeline")
    features: Dict[str, float] = Field(..., description="Key-value numeric features")


class OutcomePrediction(BaseContract):
    """
    ML evaluation for a specific outcome in a market.
    Matches user specification for Tennis value calculation:
    model_probability, fair_odds, bookmaker_odds, expected_value, edge, confidence.
    """
    outcome: str = Field(..., description="Outcome identifier (e.g. player_a, player_b)")
    model_probability: float = Field(..., ge=0.0, le=1.0, description="Predicted true probability P(outcome)")
    bookmaker_odds: float = Field(..., gt=1.0, description="Current bookmaker odds")
    fair_odds: float = Field(..., gt=1.0, description="Fair odds calculated as 1 / model_probability")
    edge: float = Field(..., description="Calculated edge: (model_probability * bookmaker_odds) - 1")
    expected_value: float = Field(..., description="Expected value metric (EV = model_prob * (odds - 1) - (1 - model_prob))")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model confidence score in this prediction")


class MLPrediction(BaseContract):
    """
    Full prediction packet emitted by the neural service for an event.
    """
    prediction_id: str = Field(..., description="Unique ID of this prediction")
    event_id: str = Field(..., description="Canonical event ID")
    sport_code: str = Field(default="tennis", description="Sport code")
    model_version: str = Field(..., description="Registered version of the ML model checkpoint")
    market: str = Field(default="match_winner", description="Market evaluated (e.g. match_winner)")
    outcomes: List[OutcomePrediction] = Field(..., min_length=1, description="Evaluated outcomes for the market")
    best_outcome: Optional[str] = Field(default=None, description="Recommended outcome with highest edge, if any")
    has_positive_edge: bool = Field(default=False, description="True if any outcome exceeds min edge threshold")
