from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import Field
from .base import BaseContract, utc_now


class TennisPointEvent(BaseContract):
    order: int
    set: Optional[int] = None
    state_before: Optional[Any] = None
    state_after: Optional[Any] = None
    game_score: Optional[List[int]] = None
    point_score: Optional[List[str]] = None
    server: Optional[Literal[0, 1]] = None
    winner: Optional[Literal[0, 1]] = None
    confirmed: bool = False
    retrospective: bool = True
    quality: str


class TennisPointHistory(BaseContract):
    """Versioned point-by-point source history; never a price or bet contract."""
    source: str = "flashscore"
    source_match_id: str
    event_id: Optional[str] = None
    document_id: str
    parser_version: str
    retrieved_at: datetime
    retrospective: bool = True
    events: List[TennisPointEvent] = Field(default_factory=list)
    freshness_seconds: Optional[float] = None
    collection_status: str
    incompleteness_reasons: List[str] = Field(default_factory=list)


class TennisForecastScenario(BaseContract):
    path: str
    probability: float = Field(..., ge=0.0, le=1.0)
    state: Dict[str, Any]


class TennisForecast(BaseContract):
    """An odds-free, point-in-time tennis forecast and its score scenarios."""
    forecast_id: str
    event_id: str
    model_name: str
    model_version: str
    observed_at: datetime
    calculated_at: datetime = Field(default_factory=utc_now)
    calibration_version: Optional[str] = None
    state: Dict[str, Any]
    probabilities: Dict[str, float]
    scenarios: Dict[str, List[TennisForecastScenario]]
    data_quality: float = Field(..., ge=0.0, le=1.0)
    data_quality_detail: Dict[str, Any] = Field(default_factory=dict)
    model_uncertainty: Dict[str, float] = Field(default_factory=dict)
    player_metrics: Dict[str, Any] = Field(default_factory=dict)
    quality_reasons: List[str] = Field(default_factory=list)
    forecast_available: bool = True
    unavailable_reason: Optional[str] = None
    feature_version: str

    @classmethod
    def from_forecast(
        cls, event_id: str, value: Dict[str, Any], observed_at: datetime,
        feature_version: str, forecast_id: str,
    ):
        return cls(
            forecast_id=forecast_id, event_id=event_id,
            model_name=value["model_name"], model_version=value["model_version"],
            observed_at=observed_at, calculated_at=value.get("calculated_at", utc_now()),
            calibration_version=value.get("calibration_version"),
            state=value["state"], probabilities=value["probabilities"],
            scenarios={
                "next_3_points": [TennisForecastScenario(path=item["points"], probability=item["probability"], state=item["state"]) for item in value["scenarios"]["next_3_points"]],
                "next_2_games": [TennisForecastScenario(path=item["games"], probability=item["probability"], state=item["state"]) for item in value["scenarios"]["next_2_games"]],
            }, data_quality=value["data_quality"], quality_reasons=value.get("reasons", []),
            data_quality_detail=value.get("data_quality_detail", {}),
            model_uncertainty=value.get("model_uncertainty", {}),
            player_metrics=value.get("player_metrics", {}),
            forecast_available=value.get("forecast_available", True), unavailable_reason=value.get("unavailable_reason"),
            feature_version=feature_version,
        )


class FeatureVector(BaseContract):
    """
    Normalized numerical feature vector prepared for ML inference.
    Strictly free from data leakage (only historical / at-the-moment features).
    """
    event_id: str = Field(..., description="Canonical event ID")
    sport_code: str = Field(default="tennis", description="Sport code")
    feature_version: str = Field(default="1.0.0", description="Version of feature engineering pipeline")
    feature_cutoff_timestamp: Optional[datetime] = Field(default=None, description="Point-in-time timestamp cutoff ensuring zero data leakage")
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
    is_shadow: bool = Field(default=False, description="True if prediction was generated by a shadow model")
