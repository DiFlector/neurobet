import math
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from .config import BacktestConfig


def no_vig_probability(odds_a: float, odds_b: float, side: str) -> Optional[float]:
    """Implied probability of one side after removing the bookmaker margin."""
    if odds_a <= 1.0 or odds_b <= 1.0:
        return None
    share = (1.0 / odds_a) / ((1.0 / odds_a) + (1.0 / odds_b))
    return share if side == "p1" else 1.0 - share


def logit_clv(close_probability: Optional[float], execution_probability: Optional[float]) -> Optional[float]:
    """Positive when the executed no-vig price was better than the closing price."""
    if close_probability is None or execution_probability is None:
        return None

    def _logit(probability: float) -> float:
        clipped = min(max(probability, 1e-4), 1.0 - 1e-4)
        return math.log(clipped / (1.0 - clipped))

    return _logit(close_probability) - _logit(execution_probability)


def quote_as_of(odds_snapshots: List["SimulatedOddsSnapshot"], as_of: datetime) -> Optional["SimulatedOddsSnapshot"]:
    available = [
        snap for snap in odds_snapshots
        if snap.observed_at <= as_of and not snap.is_suspended and snap.p1_odds > 1.0 and snap.p2_odds > 1.0
    ]
    if not available:
        return None
    return max(available, key=lambda snap: snap.observed_at)


@dataclass
class SimulatedOddsSnapshot:
    """Represents a historical odds snapshot point for market simulation."""
    snapshot_id: str
    observed_at: datetime
    p1_odds: float
    p2_odds: float
    is_suspended: bool = False


@dataclass
class OrderExecutionResult:
    """Outcome of order execution at simulated execution time."""
    is_executed: bool
    rejection_code: Optional[str]  # None, "STALE_ODDS", "MARKET_SUSPENDED", "PRICE_SLIPPED"
    decision_timestamp: datetime
    execution_timestamp: datetime
    decision_odds: float
    execution_odds: float
    simulated_latency_sec: float
    model_probability: float
    effective_edge: float


class ExecutionSimulator:
    """
    Realistic execution engine accounting for network latency,
    market suspensions, stale odds, and adverse price slippage.
    """

    def __init__(self, config: BacktestConfig):
        self.config = config

    def execute_order(
        self,
        decision_time: datetime,
        model_probability: float,
        target_outcome: str,  # "p1" or "p2"
        decision_odds: float,
        odds_snapshots: List[SimulatedOddsSnapshot],
    ) -> OrderExecutionResult:
        """
        Simulate order placement at decision_time with latency and realistic market checks.
        
        Args:
            decision_time: timestamp when ML generated the bet recommendation.
            model_probability: estimated probability for target_outcome.
            target_outcome: "p1" or "p2".
            decision_odds: odds observed at decision_time.
            odds_snapshots: full historical stream of snapshots for this event.
        """
        latency_sec = self.config.execution_latency_sec
        execution_time = decision_time + timedelta(seconds=latency_sec)

        # 1. As-of odds lookup: only snapshots with observed_at <= execution_time
        available_snapshots = [
            s for s in odds_snapshots if s.observed_at <= execution_time
        ]

        if not available_snapshots:
            return OrderExecutionResult(
                is_executed=False,
                rejection_code="NO_ODDS_SNAPSHOT",
                decision_timestamp=decision_time,
                execution_timestamp=execution_time,
                decision_odds=decision_odds,
                execution_odds=0.0,
                simulated_latency_sec=latency_sec,
                model_probability=model_probability,
                effective_edge=0.0,
            )

        # Sort by observed_at to find the latest valid snapshot as-of execution_time
        latest_snapshot = max(available_snapshots, key=lambda s: s.observed_at)

        # 2. Check market suspension
        if latest_snapshot.is_suspended:
            return OrderExecutionResult(
                is_executed=False,
                rejection_code="MARKET_SUSPENDED",
                decision_timestamp=decision_time,
                execution_timestamp=execution_time,
                decision_odds=decision_odds,
                execution_odds=0.0,
                simulated_latency_sec=latency_sec,
                model_probability=model_probability,
                effective_edge=0.0,
            )

        # 3. Check stale odds
        staleness_sec = (execution_time - latest_snapshot.observed_at).total_seconds()
        if staleness_sec > self.config.max_odds_staleness_sec:
            return OrderExecutionResult(
                is_executed=False,
                rejection_code="STALE_ODDS",
                decision_timestamp=decision_time,
                execution_timestamp=execution_time,
                decision_odds=decision_odds,
                execution_odds=0.0,
                simulated_latency_sec=latency_sec,
                model_probability=model_probability,
                effective_edge=0.0,
            )

        # Extract execution odds for target outcome
        curr_odds = latest_snapshot.p1_odds if target_outcome == "p1" else latest_snapshot.p2_odds
        if curr_odds <= 1.0:
            return OrderExecutionResult(
                is_executed=False,
                rejection_code="INVALID_ODDS",
                decision_timestamp=decision_time,
                execution_timestamp=execution_time,
                decision_odds=decision_odds,
                execution_odds=curr_odds,
                simulated_latency_sec=latency_sec,
                model_probability=model_probability,
                effective_edge=0.0,
            )

        # 4. Check slippage / adverse price movement
        effective_edge = (model_probability * curr_odds) - 1.0

        if self.config.allow_slippage and effective_edge < self.config.min_edge:
            return OrderExecutionResult(
                is_executed=False,
                rejection_code="PRICE_SLIPPED",
                decision_timestamp=decision_time,
                execution_timestamp=execution_time,
                decision_odds=decision_odds,
                execution_odds=curr_odds,
                simulated_latency_sec=latency_sec,
                model_probability=model_probability,
                effective_edge=effective_edge,
            )

        # Execution ACCEPTED
        return OrderExecutionResult(
            is_executed=True,
            rejection_code=None,
            decision_timestamp=decision_time,
            execution_timestamp=execution_time,
            decision_odds=decision_odds,
            execution_odds=curr_odds,
            simulated_latency_sec=latency_sec,
            model_probability=model_probability,
            effective_edge=effective_edge,
        )


LATENCY_GRID = (0.0, 2.0, 5.0, 10.0)


def execute_latency_grid(
    config: BacktestConfig,
    decision_time: datetime,
    model_probability: float,
    target_outcome: str,
    decision_odds: float,
    odds_snapshots: List[SimulatedOddsSnapshot],
) -> Dict[float, str]:
    """Rejection code at each planned latency. An accepted order is recorded as executed."""
    report: Dict[float, str] = {}
    for latency in LATENCY_GRID:
        trial = config.model_copy(update={"execution_latency_sec": latency})
        result = ExecutionSimulator(trial).execute_order(
            decision_time,
            model_probability,
            target_outcome,
            decision_odds,
            odds_snapshots,
        )
        report[latency] = "executed" if result.is_executed else str(result.rejection_code or "rejected")
    return report
