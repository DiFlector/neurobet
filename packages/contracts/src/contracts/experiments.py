"""
Scientific Experiment Contracts for Neurobet according to Architecture Section 47.
Defines schemas for backtesting benchmarks, statistical metrics, calibration curves, and financial evaluation.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import Field
from .base import BaseContract, utc_now


class CalibrationPoint(BaseContract):
    """Single bin coordinate on empirical reliability diagram."""
    bin_index: int = Field(..., ge=0, description="Bin index (0-based)")
    bin_range: str = Field(..., description="Probability range of bin, e.g. [0.4, 0.5)")
    mean_predicted_prob: float = Field(..., ge=0.0, le=1.0, description="Average model confidence in this bin")
    fraction_positives: float = Field(..., ge=0.0, le=1.0, description="Empirical win rate / positive outcome fraction")
    sample_count: int = Field(..., ge=0, description="Number of predictions in bin")


class CalibrationCurveReport(BaseContract):
    """Comprehensive probability calibration report."""
    n_bins: int = Field(default=10, description="Number of bins used")
    points: List[CalibrationPoint] = Field(default_factory=list, description="Binned calibration curve points")
    expected_calibration_error: float = Field(..., ge=0.0, description="Expected Calibration Error (ECE)")
    brier_score: float = Field(..., ge=0.0, le=1.0, description="Overall Brier score")
    log_loss: float = Field(..., ge=0.0, description="Binary cross-entropy / log loss")


class ScientificExperimentConfig(BaseContract):
    """
    Fixed parameters for a scientific experiment run.
    Mandatory baseline configuration for Phase 23 & Phase 24 comparisons.
    """
    experiment_name: str = Field(..., description="Unique experiment descriptor")
    sport_code: str = Field(default="tennis", description="Target sport code")
    market: str = Field(default="match_winner", description="Market under evaluation")
    period_start: str = Field(..., description="Evaluation start timestamp ISO")
    period_end: str = Field(..., description="Evaluation end timestamp ISO")
    
    # Financial & bankroll parameters
    initial_bankroll: float = Field(default=100000.0, gt=0.0, description="Starting virtual bankroll in RUB")
    stake_strategy: Literal["FIXED", "PERCENT", "KELLY"] = Field(default="KELLY", description="Staking policy")
    kelly_fraction: float = Field(default=0.25, gt=0.0, le=1.0, description="Fractional Kelly multiplier")
    max_stake_cap: float = Field(default=0.05, gt=0.0, le=0.5, description="Max stake cap as fraction of bankroll")
    min_stake: float = Field(default=100.0, gt=0.0, description="Minimum stake amount in RUB")
    
    # Strategy parameters
    min_edge: float = Field(default=0.03, ge=0.0, description="Minimum required value/edge threshold")
    min_probability: float = Field(default=0.40, ge=0.0, le=1.0, description="Minimum model probability")
    max_odds: float = Field(default=10.0, gt=1.0, description="Maximum bookmaker odds")
    
    # Execution parameters
    execution_latency_sec: float = Field(default=2.0, ge=0.0, description="Simulated execution latency in seconds")
    max_odds_staleness_sec: float = Field(default=15.0, gt=0.0, description="Max odds staleness cap")
    allow_slippage: bool = Field(default=True, description="Enable realistic adverse price slippage")
    
    # ML & Backtest setup
    model_type: str = Field(default="gradient_boosting", description="ML model architecture")
    use_llm: bool = Field(default=False, description="Whether LLM context reasoning is enabled")
    n_splits: int = Field(default=4, ge=2, description="Number of walk-forward folds")
    purge_window_sec: float = Field(default=300.0, ge=0.0, description="Purge window in seconds")
    random_seed: int = Field(default=42, description="Deterministic random seed")


class ScientificExperimentResultContract(BaseContract):
    """
    Complete output report of a scientific baseline experiment.
    Captures financial, risk, statistical, and calibration performance.
    """
    id: Optional[str] = Field(default=None, description="Experiment result UUID")
    name: str = Field(..., description="Experiment name")
    config: ScientificExperimentConfig = Field(..., description="Fixed experiment configuration")
    
    # Financial metrics
    initial_bankroll: float = Field(..., description="Initial bankroll in RUB")
    final_bankroll: float = Field(..., description="Final bankroll in RUB")
    net_pnl: float = Field(..., description="Net profit or loss in RUB")
    roi: float = Field(..., description="Return on investment (net_pnl / turnover)")
    turnover: float = Field(..., description="Total cumulative stake turnover in RUB")
    win_rate: float = Field(..., description="Fraction of settled bets won")
    
    # Bet count metrics
    total_bets: int = Field(..., ge=0, description="Total bets placed")
    bets_won: int = Field(..., ge=0, description="Bets won")
    bets_lost: int = Field(..., ge=0, description="Bets lost")
    bets_void: int = Field(..., ge=0, description="Bets voided")
    
    # Risk metrics
    peak_bankroll: float = Field(..., description="Peak bankroll equity achieved")
    min_bankroll: float = Field(..., description="Minimum bankroll equity reached")
    peak_exposure: float = Field(..., description="Peak active locked risk exposure")
    max_drawdown_amount: float = Field(..., description="Max drawdown in RUB")
    max_drawdown_pct: float = Field(..., description="Max drawdown as fraction [0.0, 1.0]")
    
    # Statistical & Calibration metrics
    log_loss: float = Field(..., description="Out-of-sample log loss / binary cross-entropy")
    brier_score: float = Field(..., description="Out-of-sample Brier score")
    roc_auc: float = Field(..., description="Out-of-sample ROC AUC")
    expected_calibration_error: float = Field(..., description="Expected Calibration Error (ECE)")
    calibration_curve: CalibrationCurveReport = Field(..., description="Binned calibration curve data")
    
    # Odds & Edge breakdowns
    odds_buckets: Dict[str, Any] = Field(default_factory=dict, description="Performance broken down by odds range")
    edge_buckets: Dict[str, Any] = Field(default_factory=dict, description="Performance broken down by edge range")
    
    created_at: datetime = Field(default_factory=utc_now, description="Execution completion timestamp")
