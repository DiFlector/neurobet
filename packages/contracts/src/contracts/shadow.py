from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import Field
from .base import BaseContract, utc_now
from .predictions import MLPrediction


class ShadowConfig(BaseContract):
    """
    Configuration parameters for Model Shadow Mode evaluation.
    Governs shadow period requirements, parallel observation thresholds,
    and empirical promotion gates.
    """
    min_shadow_period_days: int = Field(
        default=7,
        ge=1,
        description="Minimum mandatory calendar days candidate must remain in SHADOW before promotion",
    )
    min_shadow_evaluations: int = Field(
        default=100,
        ge=10,
        description="Minimum number of parallel event evaluations before promotion eligibility",
    )
    max_acceptable_drift_psi: float = Field(
        default=0.25,
        ge=0.0,
        description="Maximum acceptable Population Stability Index (PSI) drift before warning/blocking",
    )
    min_shadow_roi: float = Field(
        default=0.0,
        description="Minimum counterfactual ROI required for promotion",
    )
    max_acceptable_brier_delta: float = Field(
        default=0.02,
        description="Maximum allowed increase in Brier score compared to active baseline",
    )
    active_model_version: str = Field(
        default="v1.0.0_baseline",
        description="Identifier of currently active production model",
    )
    shadow_model_version: str = Field(
        default="v1.1.0_candidate",
        description="Identifier of candidate model running in shadow mode",
    )
    sport_code: str = Field(default="tennis", description="Target sport code")
    market: str = Field(default="match_winner", description="Evaluated market")


class ShadowPredictionPair(BaseContract):
    """
    Side-by-side comparison point of Active vs Shadow model inference for a single event.
    """
    event_id: str = Field(..., description="Canonical event ID")
    timestamp: datetime = Field(default_factory=utc_now, description="Evaluation timestamp")
    active_prediction: MLPrediction = Field(..., description="Prediction emitted by ACTIVE model")
    shadow_prediction: MLPrediction = Field(..., description="Prediction emitted by SHADOW model")
    prob_diff_outcome_a: float = Field(
        ...,
        description="Absolute difference in predicted probability for Player A: |P_active - P_shadow|",
    )
    outcomes_match: bool = Field(
        ...,
        description="True if both active and shadow models recommend the same winning outcome",
    )
    edge_diff: float = Field(
        ...,
        description="Difference in edge: edge_shadow - edge_active",
    )


class DriftAnalysisReport(BaseContract):
    """
    Quantitative analysis of prediction output drift between Active and Shadow models.
    Uses Population Stability Index (PSI), Wasserstein Distance, and mean probability shift.
    """
    probability_drift_psi: float = Field(
        ...,
        description="Population Stability Index (PSI) across probability distribution bins",
    )
    wasserstein_distance: float = Field(
        ...,
        description="Wasserstein (Earth Mover's) distance between predicted probability distributions",
    )
    mean_prob_shift: float = Field(
        ...,
        description="Mean difference in predicted probabilities (P_shadow - P_active)",
    )
    agreement_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Fraction of events where active and shadow models pick the same best outcome",
    )
    drift_status: Literal["STABLE", "MODERATE_DRIFT", "SEVERE_DRIFT"] = Field(
        ...,
        description="Qualitative drift category based on standard PSI thresholds (<0.10 stable, 0.10-0.25 moderate, >0.25 severe)",
    )
    psi_by_bin: Dict[str, float] = Field(
        default_factory=dict,
        description="Detailed PSI contributions across distribution bins",
    )


class SegmentPerformance(BaseContract):
    """
    Counterfactual betting performance metrics for a specific segment/slice.
    """
    segment_name: str = Field(..., description="Name of segment (e.g. '1.50 - 2.00 odds')")
    evaluations_count: int = Field(default=0, ge=0, description="Total events evaluated in this segment")
    counterfactual_bets_count: int = Field(default=0, ge=0, description="Bets shadow model would have placed")
    counterfactual_turnover: float = Field(default=0.0, description="Total virtual stake turnover in RUB")
    counterfactual_pnl: float = Field(default=0.0, description="Net counterfactual profit/loss in RUB")
    counterfactual_roi: float = Field(default=0.0, description="Counterfactual ROI (pnl / turnover)")
    counterfactual_win_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="Counterfactual win rate")
    avg_edge: float = Field(default=0.0, description="Average estimated edge")
    avg_odds: float = Field(default=0.0, description="Average odds")
    brier_score: float = Field(default=0.0, description="Brier calibration score for segment")
    log_loss: float = Field(default=0.0, description="Log loss for segment")


class ShadowEvaluationReport(BaseContract):
    """
    Comprehensive Shadow Mode audit report comparing Active production model and Shadow candidate.
    Includes drift calculations, segment breakdowns, and promotion decision gate.
    """
    id: Optional[str] = Field(default=None, description="Evaluation report identifier")
    active_model_version: str = Field(..., description="Active production model version")
    shadow_model_version: str = Field(..., description="Shadow candidate model version")
    sport_code: str = Field(default="tennis", description="Sport code")
    market: str = Field(default="match_winner", description="Market evaluated")
    
    shadow_period_start: datetime = Field(..., description="Shadow observation start timestamp")
    shadow_period_end: datetime = Field(..., description="Shadow observation end timestamp")
    shadow_duration_hours: float = Field(..., description="Cumulative duration of shadow observation in hours")
    shadow_duration_days: float = Field(..., description="Cumulative duration in days")
    
    total_evaluations: int = Field(..., ge=0, description="Total parallel evaluations performed")
    active_total_bets: int = Field(..., ge=0, description="Bets placed by active model")
    shadow_counterfactual_bets: int = Field(..., ge=0, description="Bets shadow model would have placed")
    
    active_pnl: float = Field(..., description="Active model PnL (RUB)")
    shadow_counterfactual_pnl: float = Field(..., description="Shadow model counterfactual PnL (RUB)")
    active_roi: float = Field(..., description="Active model ROI")
    shadow_counterfactual_roi: float = Field(..., description="Shadow model counterfactual ROI")
    
    # Statistical and calibration comparisons
    active_brier_score: float = Field(default=0.0, description="Active model Brier score")
    shadow_brier_score: float = Field(default=0.0, description="Shadow model Brier score")
    active_log_loss: float = Field(default=0.0, description="Active model Log loss")
    shadow_log_loss: float = Field(default=0.0, description="Shadow model Log loss")
    
    drift_analysis: DriftAnalysisReport = Field(..., description="Prediction drift and distribution comparison")
    performance_by_odds_segment: Dict[str, SegmentPerformance] = Field(
        default_factory=dict,
        description="Segmented performance across odds tiers",
    )
    performance_by_edge_segment: Dict[str, SegmentPerformance] = Field(
        default_factory=dict,
        description="Segmented performance across edge tiers",
    )
    
    # Promotion Acceptance Gate
    meets_promotion_criteria: bool = Field(
        ...,
        description="True ONLY if minimum shadow period is met, drift is stable, and ROI/calibration are superior or non-degrading",
    )
    promotion_rejection_reasons: List[str] = Field(
        default_factory=list,
        description="Specific reasons if candidate is disqualified from promotion",
    )
    verdict: str = Field(..., description="Summary verdict and promotion recommendation")
    created_at: datetime = Field(default_factory=utc_now)
