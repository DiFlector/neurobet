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
    freshness_buckets: Dict[str, Any] = Field(default_factory=dict, description="Performance broken down by research freshness")
    
    created_at: datetime = Field(default_factory=utc_now, description="Execution completion timestamp")


class FreshnessBucketMetrics(BaseContract):
    """Performance metrics segmented by research evidence freshness."""
    bucket_name: str = Field(..., description="Freshness category, e.g. <1h, 1-6h, >6h, none")
    total_bets: int = Field(default=0, ge=0)
    bets_won: int = Field(default=0, ge=0)
    bets_lost: int = Field(default=0, ge=0)
    bets_void: int = Field(default=0, ge=0)
    win_rate: float = Field(default=0.0)
    turnover: float = Field(default=0.0)
    net_pnl: float = Field(default=0.0)
    roi: float = Field(default=0.0)
    avg_odds: float = Field(default=0.0)
    avg_edge: float = Field(default=0.0)


class LLMErrorAnalysis(BaseContract):
    """Deep error breakdown of qualitative LLM approvals and vetoes."""
    total_candidates_evaluated: int = Field(default=0, ge=0, description="Total proposals evaluated by LLM")
    veto_count: int = Field(default=0, ge=0, description="Total proposals vetoed/filtered by LLM")
    true_negatives: int = Field(default=0, ge=0, description="Vetoed proposals that would have LOST (losses successfully avoided)")
    false_negatives: int = Field(default=0, ge=0, description="Vetoed proposals that would have WON (missed wins / opportunity cost)")
    veto_precision: float = Field(default=0.0, ge=0.0, le=1.0, description="Ratio of true negatives to total vetoes")
    
    approved_count: int = Field(default=0, ge=0, description="Total proposals approved by LLM")
    approval_count: int = Field(default=0, ge=0, description="Alias for approved_count")
    true_positives: int = Field(default=0, ge=0, description="Approved proposals that WON")
    false_positives: int = Field(default=0, ge=0, description="Approved proposals that LOST")
    approval_precision: float = Field(default=0.0, ge=0.0, le=1.0, description="Win rate among LLM-approved bets")
    
    losses_avoided_amount: float = Field(default=0.0, description="Estimated capital preserved by avoiding losses (RUB)")
    avoided_loss_pnl: float = Field(default=0.0, description="Alias for losses_avoided_amount")
    profits_forgone_amount: float = Field(default=0.0, description="Estimated potential profit missed due to false vetoes (RUB)")
    missed_profit_pnl: float = Field(default=0.0, description="Alias for profits_forgone_amount")
    net_veto_value_pnl: float = Field(default=0.0, description="Economic value created by vetoes (losses avoided - profits forgone)")


class LLMExperimentComparisonReport(BaseContract):
    """
    Formal scientific comparison between ML-only baseline and ML+LLM experiment.
    Evaluates incremental value, statistical significance, and risk deltas.
    """
    id: Optional[str] = Field(default=None, description="Comparison report UUID")
    name: str = Field(..., description="Comparison experiment name")
    baseline_name: str = Field(..., description="Baseline ML-only experiment identifier")
    llm_experiment_name: str = Field(..., description="ML + LLM experiment identifier")
    sport_code: str = Field(default="tennis")
    market: str = Field(default="match_winner")
    period_start: str = Field(...)
    period_end: str = Field(...)
    
    # Financial & Risk comparison summaries
    baseline_summary: Dict[str, Any] = Field(default_factory=dict, description="Condensed ML-only performance summary")
    llm_summary: Dict[str, Any] = Field(default_factory=dict, description="Condensed ML+LLM performance summary")
    
    # Delta metrics: (ML+LLM) - (ML-only)
    delta_total_bets: int = Field(..., description="Change in total bets placed")
    delta_turnover: float = Field(..., description="Change in turnover (RUB)")
    delta_pnl: float = Field(..., description="Incremental PnL (RUB)")
    delta_roi: float = Field(..., description="Change in ROI (percentage points)")
    delta_win_rate: float = Field(..., description="Change in Win Rate (percentage points)")
    delta_max_drawdown_amount: float = Field(default=0.0, description="Change in max drawdown (RUB)")
    delta_max_drawdown_pct: float = Field(..., description="Change in max drawdown percentage")
    
    # Statistical & Calibration Deltas
    delta_brier_score: float = Field(..., description="Change in Brier score (negative is better calibration)")
    delta_log_loss: float = Field(..., description="Change in Log Loss (negative is better calibration)")
    delta_ece: float = Field(..., description="Change in Expected Calibration Error")
    
    # Segmented breakdowns
    edge_bucket_comparison: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Side-by-side edge bucket stats")
    freshness_breakdown: Dict[str, FreshnessBucketMetrics] = Field(default_factory=dict, description="Performance by research age")
    
    # Error analysis and Independent Information Value
    error_analysis: LLMErrorAnalysis = Field(..., description="Analysis of LLM mistakes and veto accuracy")
    has_independent_information: bool = Field(default=True, description="True if LLM provides non-redundant predictive signal")
    is_overall_improvement: bool = Field(..., description="Strict acceptance: True only if empirical evidence supports benefit")
    conclusion: str = Field(..., description="Scientific verdict and deployment recommendation")
    
    created_at: datetime = Field(default_factory=utc_now)

