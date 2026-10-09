from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
import numpy as np
from .bankroll import SimulatedBet, EquityPoint


@dataclass
class BucketMetrics:
    """Performance metrics for a specific slice/bucket."""
    bucket_name: str
    total_bets: int = 0
    bets_won: int = 0
    bets_lost: int = 0
    bets_void: int = 0
    win_rate: float = 0.0
    turnover: float = 0.0
    net_pnl: float = 0.0
    roi: float = 0.0
    avg_odds: float = 0.0
    avg_edge: float = 0.0


@dataclass
class BacktestSummary:
    """Full quantitative performance report of a backtest run."""
    initial_bankroll: float
    final_bankroll: float
    net_pnl: float
    roi: float  # net_pnl / turnover
    turnover: float
    win_rate: float
    
    total_bets: int
    bets_won: int
    bets_lost: int
    bets_void: int
    
    peak_bankroll: float
    min_bankroll: float
    peak_exposure: float
    max_drawdown_amount: float
    max_drawdown_pct: float  # e.g. 0.125 for 12.5%
    
    # Statistical & Probability Calibration Metrics
    log_loss: float = 0.0
    brier_score: float = 0.0
    roc_auc: float = 0.0
    expected_calibration_error: float = 0.0
    calibration_curve: Dict[str, Any] = field(default_factory=dict)
    
    odds_buckets: Dict[str, BucketMetrics] = field(default_factory=dict)
    edge_buckets: Dict[str, BucketMetrics] = field(default_factory=dict)



class BacktestMetricsCalculator:
    """
    Computes financial metrics, drawdown curves, and bucketed statistics.
    """

    @staticmethod
    def compute_drawdown(equity_curve: List[EquityPoint]) -> Tuple[float, float]:
        """
        Calculate maximum peak-to-trough drawdown in absolute currency and percentage.
        Returns: (max_drawdown_amount, max_drawdown_pct)
        """
        if not equity_curve:
            return 0.0, 0.0

        equities = np.array([ep.equity for ep in equity_curve], dtype=np.float64)
        if len(equities) == 0:
            return 0.0, 0.0

        running_max = np.maximum.accumulate(equities)
        drawdowns = running_max - equities
        
        # Avoid division by zero
        safe_running_max = np.where(running_max > 0, running_max, 1.0)
        drawdown_pcts = drawdowns / safe_running_max

        max_dd_amount = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0
        max_dd_pct = float(np.max(drawdown_pcts)) if len(drawdown_pcts) > 0 else 0.0

        return round(max_dd_amount, 2), round(max_dd_pct, 4)

    @classmethod
    def compute_summary(
        cls,
        initial_bankroll: float,
        final_bankroll: float,
        settled_bets: List[SimulatedBet],
        equity_curve: List[EquityPoint],
        peak_exposure: float,
        statistical_metrics: Optional[Dict[str, float]] = None,
        calibration_curve: Optional[Dict[str, Any]] = None,
    ) -> BacktestSummary:

        """
        Compute complete BacktestSummary from settled bets and equity history.
        """
        total_bets = len(settled_bets)
        bets_won = sum(1 for b in settled_bets if b.status == "WON")
        bets_lost = sum(1 for b in settled_bets if b.status == "LOST")
        bets_void = sum(1 for b in settled_bets if b.status == "VOID")

        decisive_bets = bets_won + bets_lost
        win_rate = (bets_won / decisive_bets) if decisive_bets > 0 else 0.0

        turnover = sum(b.stake for b in settled_bets)
        net_pnl = sum(b.net_profit for b in settled_bets)
        roi = (net_pnl / turnover) if turnover > 0 else 0.0

        equities = [ep.equity for ep in equity_curve] if equity_curve else [initial_bankroll]
        peak_bankroll = max(equities)
        min_bankroll = min(equities)

        max_dd_amount, max_dd_pct = cls.compute_drawdown(equity_curve)

        # 1. Odds Bucketing: [1.0, 1.5), [1.5, 2.0), [2.0, 3.0), [3.0, +inf)
        odds_bins = {
            "1.01 - 1.50": lambda b: 1.01 <= b.odds < 1.50,
            "1.50 - 2.00": lambda b: 1.50 <= b.odds < 2.00,
            "2.00 - 3.00": lambda b: 2.00 <= b.odds < 3.00,
            "3.00+": lambda b: b.odds >= 3.00,
        }
        odds_buckets = {
            name: cls._compute_bucket(name, [b for b in settled_bets if condition(b)])
            for name, condition in odds_bins.items()
        }

        # 2. Edge Bucketing: [0.0, 0.05), [0.05, 0.10), [0.10, +inf)
        edge_bins = {
            "2% - 5% edge": lambda b: 0.02 <= b.edge < 0.05,
            "5% - 10% edge": lambda b: 0.05 <= b.edge < 0.10,
            "10%+ edge": lambda b: b.edge >= 0.10,
        }
        edge_buckets = {
            name: cls._compute_bucket(name, [b for b in settled_bets if condition(b)])
            for name, condition in edge_bins.items()
        }

        return BacktestSummary(
            initial_bankroll=round(initial_bankroll, 2),
            final_bankroll=round(final_bankroll, 2),
            net_pnl=round(net_pnl, 2),
            roi=round(roi, 4),
            turnover=round(turnover, 2),
            win_rate=round(win_rate, 4),
            total_bets=total_bets,
            bets_won=bets_won,
            bets_lost=bets_lost,
            bets_void=bets_void,
            peak_bankroll=round(peak_bankroll, 2),
            min_bankroll=round(min_bankroll, 2),
            peak_exposure=round(peak_exposure, 2),
            max_drawdown_amount=max_dd_amount,
            max_drawdown_pct=max_dd_pct,
            log_loss=round((statistical_metrics or {}).get("log_loss", 0.0), 4),
            brier_score=round((statistical_metrics or {}).get("brier_score", 0.0), 4),
            roc_auc=round((statistical_metrics or {}).get("roc_auc", 0.0), 4),
            expected_calibration_error=round((statistical_metrics or {}).get("expected_calibration_error", 0.0), 4),
            calibration_curve=calibration_curve or {},
            odds_buckets=odds_buckets,
            edge_buckets=edge_buckets,
        )


    @staticmethod
    def _compute_bucket(name: str, bets: List[SimulatedBet]) -> BucketMetrics:
        """Helper to calculate metrics for a subset of bets."""
        if not bets:
            return BucketMetrics(bucket_name=name)

        total = len(bets)
        won = sum(1 for b in bets if b.status == "WON")
        lost = sum(1 for b in bets if b.status == "LOST")
        void_cnt = sum(1 for b in bets if b.status == "VOID")
        decisive = won + lost

        wr = (won / decisive) if decisive > 0 else 0.0
        turnover = sum(b.stake for b in bets)
        net_pnl = sum(b.net_profit for b in bets)
        roi = (net_pnl / turnover) if turnover > 0 else 0.0
        avg_odds = sum(b.odds for b in bets) / total
        avg_edge = sum(b.edge for b in bets) / total

        return BucketMetrics(
            bucket_name=name,
            total_bets=total,
            bets_won=won,
            bets_lost=lost,
            bets_void=void_cnt,
            win_rate=round(wr, 4),
            turnover=round(turnover, 2),
            net_pnl=round(net_pnl, 2),
            roi=round(roi, 4),
            avg_odds=round(avg_odds, 2),
            avg_edge=round(avg_edge, 4),
        )
