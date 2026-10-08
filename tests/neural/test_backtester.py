"""Unit and integration tests for Phase 10: Walk-forward backtester."""

import sys
from datetime import datetime, timedelta
import numpy as np

from app.backtest.config import BacktestConfig
from app.backtest.temporal import WalkForwardSplitter, ChronologicalSplitError
from app.backtest.execution import ExecutionSimulator, SimulatedOddsSnapshot, OrderExecutionResult
from app.backtest.bankroll import BacktestBankroll, SimulatedBet, EquityPoint
from app.backtest.metrics import BacktestMetricsCalculator, BacktestSummary
from app.backtest.engine import WalkForwardBacktester, generate_synthetic_backtest_samples
from db import SessionLocal
from db.models.ml_registry import ExperimentResult


def test_chronological_split_and_purge():
    """Verify strictly forward-looking chronological splitting with purge."""
    base_time = datetime(2026, 6, 1, 12, 0, 0)
    timestamps = [base_time + timedelta(minutes=10 * i) for i in range(50)]

    splitter = WalkForwardSplitter(n_splits=3, purge_window_sec=300.0)
    splits = splitter.split(timestamps)

    assert len(splits) == 3, f"Expected 3 splits, got {len(splits)}"

    time_floats = np.array([t.timestamp() for t in timestamps])

    for fold_idx, (train_idx, test_idx) in enumerate(splits):
        assert len(train_idx) > 0
        assert len(test_idx) > 0

        # No intersection between train and test
        assert len(np.intersect1d(train_idx, test_idx)) == 0

        max_train = np.max(time_floats[train_idx])
        min_test = np.min(time_floats[test_idx])

        # Strict chronological order
        assert max_train < min_test, f"Fold {fold_idx}: Train time {max_train} >= Test time {min_test}"

        # Purge buffer enforced (300 seconds)
        assert max_train + 300.0 <= min_test + 1e-5, f"Fold {fold_idx}: Purge buffer violated"

    # Test that random lookahead violates validation and raises ChronologicalSplitError
    try:
        splitter.validate_no_leakage(
            train_idx=np.array([10, 20]),
            test_idx=np.array([5, 15]),  # test sample 5 is in the past!
            time_floats=time_floats,
        )
        assert False, "Should have raised ChronologicalSplitError for backward lookahead"
    except ChronologicalSplitError:
        pass

    print("test_chronological_split_and_purge: OK")


def test_execution_as_of_odds_and_zero_future_leakage():
    """Verify execution engine looks up odds strictly as-of execution time."""
    config = BacktestConfig(execution_latency_sec=2.0, max_odds_staleness_sec=15.0)
    sim = ExecutionSimulator(config)

    decision_time = datetime(2026, 6, 1, 12, 0, 10)
    # Execution time = 12:00:10 + 2.0s = 12:00:12

    snapshots = [
        # Snapshot 1: past (12:00:05)
        SimulatedOddsSnapshot(snapshot_id="s1", observed_at=datetime(2026, 6, 1, 12, 0, 5), p1_odds=1.80, p2_odds=2.00),
        # Snapshot 2: right at execution time (12:00:12)
        SimulatedOddsSnapshot(snapshot_id="s2", observed_at=datetime(2026, 6, 1, 12, 0, 12), p1_odds=1.85, p2_odds=1.95),
        # Snapshot 3: future snapshot (12:00:15) - MUST NOT BE USED!
        SimulatedOddsSnapshot(snapshot_id="s3", observed_at=datetime(2026, 6, 1, 12, 0, 15), p1_odds=2.50, p2_odds=1.50),
    ]

    res = sim.execute_order(
        decision_time=decision_time,
        model_probability=0.60,
        target_outcome="p1",
        decision_odds=1.80,
        odds_snapshots=snapshots,
    )

    assert res.is_executed is True
    # Must match Snapshot 2 (1.85), NOT Snapshot 3 (2.50)
    assert res.execution_odds == 1.85, f"Expected 1.85, got {res.execution_odds}"
    assert res.execution_timestamp == datetime(2026, 6, 1, 12, 0, 12)
    print("test_execution_as_of_odds_and_zero_future_leakage: OK")


def test_execution_rejections_stale_and_suspension():
    """Verify stale odds and suspension gates reject order execution."""
    config = BacktestConfig(execution_latency_sec=2.0, max_odds_staleness_sec=10.0, min_edge=0.03)
    sim = ExecutionSimulator(config)

    decision_time = datetime(2026, 6, 1, 12, 0, 30)

    # 1. Suspended snapshot
    susp_snap = [
        SimulatedOddsSnapshot(
            snapshot_id="s_susp",
            observed_at=datetime(2026, 6, 1, 12, 0, 31),
            p1_odds=1.90,
            p2_odds=1.90,
            is_suspended=True,
        )
    ]
    res_susp = sim.execute_order(
        decision_time=decision_time,
        model_probability=0.60,
        target_outcome="p1",
        decision_odds=1.90,
        odds_snapshots=susp_snap,
    )
    assert res_susp.is_executed is False
    assert res_susp.rejection_code == "MARKET_SUSPENDED"

    # 2. Stale snapshot (> 10s old)
    stale_snap = [
        SimulatedOddsSnapshot(
            snapshot_id="s_stale",
            observed_at=datetime(2026, 6, 1, 12, 0, 10),  # 22 seconds stale
            p1_odds=1.90,
            p2_odds=1.90,
            is_suspended=False,
        )
    ]
    res_stale = sim.execute_order(
        decision_time=decision_time,
        model_probability=0.60,
        target_outcome="p1",
        decision_odds=1.90,
        odds_snapshots=stale_snap,
    )
    assert res_stale.is_executed is False
    assert res_stale.rejection_code == "STALE_ODDS"

    # 3. Adverse price slippage
    slipped_snap = [
        SimulatedOddsSnapshot(
            snapshot_id="s_slip",
            observed_at=datetime(2026, 6, 1, 12, 0, 31),
            p1_odds=1.60,  # odds dropped from 1.90 to 1.60: edge = 0.55 * 1.60 - 1 = -0.12 < 0.03
            p2_odds=2.30,
            is_suspended=False,
        )
    ]
    res_slip = sim.execute_order(
        decision_time=decision_time,
        model_probability=0.55,
        target_outcome="p1",
        decision_odds=1.90,
        odds_snapshots=slipped_snap,
    )
    assert res_slip.is_executed is False
    assert res_slip.rejection_code == "PRICE_SLIPPED"

    print("test_execution_rejections_stale_and_suspension: OK")


def test_bankroll_sizing_exposure_and_drawdown():
    """Verify position sizing math, exposure limit enforcement, and drawdown calculation."""
    config = BacktestConfig(
        initial_bankroll=10000.0,
        stake_strategy="KELLY",
        kelly_fraction=0.25,
        max_stake_cap=0.05,
        max_exposure=0.10,  # Max 10% exposure = 1000 RUB
    )
    bankroll = BacktestBankroll(config)

    # 1. Kelly sizing calculation:
    # prob=0.60, odds=2.0 -> b = 1.0, f* = (0.60*1.0 - 0.40)/1.0 = 0.20
    # Quarter kelly = 0.20 * 0.25 = 0.05 (5%)
    # 5% of 10000 = 500 RUB
    stake = bankroll.calculate_stake(probability=0.60, odds=2.0)
    assert abs(stake - 500.0) < 1.0, f"Expected 500, got {stake}"

    # 2. Place first bet (500 RUB)
    b1 = bankroll.place_bet(
        bet_id="b1",
        event_id="e1",
        target_outcome="p1",
        placed_at=datetime(2026, 6, 1, 10, 0, 0),
        odds=2.0,
        stake=500.0,
        probability=0.60,
        edge=0.20,
    )
    assert b1 is not None
    assert bankroll.current_balance == 9500.0
    assert bankroll.current_exposure == 500.0

    # 3. Place second bet (500 RUB) -> exposure reaches 1000 RUB (limit)
    b2 = bankroll.place_bet(
        bet_id="b2",
        event_id="e2",
        target_outcome="p1",
        placed_at=datetime(2026, 6, 1, 10, 1, 0),
        odds=2.0,
        stake=500.0,
        probability=0.60,
        edge=0.20,
    )
    assert b2 is not None
    assert bankroll.current_exposure == 1000.0

    # 4. Third bet breaches max exposure (1000 + 500 > 1000) -> MUST BE REJECTED!
    b3 = bankroll.place_bet(
        bet_id="b3",
        event_id="e3",
        target_outcome="p1",
        placed_at=datetime(2026, 6, 1, 10, 2, 0),
        odds=2.0,
        stake=500.0,
        probability=0.60,
        edge=0.20,
    )
    assert b3 is None, "Third bet should be rejected due to exposure limit"

    # 5. Settle b1 as WON (payout = 1000 RUB, profit = +500)
    bankroll.settle_bet("b1", "p1", datetime(2026, 6, 1, 11, 0, 0))
    # Balance = 9000 + 1000 = 10000, Exposure = 500
    assert bankroll.current_balance == 10000.0
    assert bankroll.current_exposure == 500.0

    # 6. Settle b2 as LOST (profit = -500)
    bankroll.settle_bet("b2", "p2", datetime(2026, 6, 1, 11, 30, 0))
    assert bankroll.current_balance == 10000.0
    assert bankroll.current_exposure == 0.0

    # 7. Drawdown math test
    # Curve: 10000 -> 11000 -> 9000 -> 9500
    # Peak = 11000, Trough = 9000 -> Drawdown = 2000, Pct = 2000 / 11000 = 18.18%
    mock_curve = [
        EquityPoint(timestamp=datetime(2026, 1, 1), balance=10000, exposure=0, equity=10000, total_pnl=0),
        EquityPoint(timestamp=datetime(2026, 1, 2), balance=11000, exposure=0, equity=11000, total_pnl=1000),
        EquityPoint(timestamp=datetime(2026, 1, 3), balance=9000, exposure=0, equity=9000, total_pnl=-1000),
        EquityPoint(timestamp=datetime(2026, 1, 4), balance=9500, exposure=0, equity=9500, total_pnl=-500),
    ]
    dd_amount, dd_pct = BacktestMetricsCalculator.compute_drawdown(mock_curve)
    assert dd_amount == 2000.0, f"Expected 2000, got {dd_amount}"
    assert abs(dd_pct - (2000.0 / 11000.0)) < 1e-4

    print("test_bankroll_sizing_exposure_and_drawdown: OK")


def test_end_to_end_backtest_reproducibility():
    """Verify that backtest runs are 100% deterministic given the same config and seed."""
    config = BacktestConfig(
        n_splits=3,
        random_seed=42,
        initial_bankroll=100000.0,
        stake_strategy="KELLY",
        kelly_fraction=0.25,
    )

    samples_1 = generate_synthetic_backtest_samples(n_events=100, random_seed=42)
    backtester_1 = WalkForwardBacktester(config=config)
    res_1 = backtester_1.run(samples=samples_1, model_type="gradient_boosting", save_to_db=False)

    samples_2 = generate_synthetic_backtest_samples(n_events=100, random_seed=42)
    backtester_2 = WalkForwardBacktester(config=config)
    res_2 = backtester_2.run(samples=samples_2, model_type="gradient_boosting", save_to_db=False)

    # Assert 100% bit-for-bit reproducibility
    assert res_1.final_bankroll == res_2.final_bankroll, f"{res_1.final_bankroll} != {res_2.final_bankroll}"
    assert res_1.net_pnl == res_2.net_pnl
    assert res_1.roi == res_2.roi
    assert res_1.win_rate == res_2.win_rate
    assert res_1.total_bets == res_2.total_bets
    assert res_1.max_drawdown_amount == res_2.max_drawdown_amount
    assert res_1.max_drawdown_pct == res_2.max_drawdown_pct

    # Ensure bets were actually placed
    assert res_1.total_bets > 0, "Backtest should have placed bets"
    print(f"test_end_to_end_backtest_reproducibility: OK (Total bets={res_1.total_bets}, PnL={res_1.net_pnl:+.2f} RUB, ROI={res_1.roi*100:.2f}%)")


def test_database_experiment_recording():
    """Verify ExperimentResult row persistence in PostgreSQL."""
    config = BacktestConfig(n_splits=2, random_seed=42, initial_bankroll=50000.0)
    samples = generate_synthetic_backtest_samples(n_events=50, random_seed=42)
    backtester = WalkForwardBacktester(config=config)

    summary = backtester.run(samples=samples, model_type="logistic_regression", save_to_db=True)

    with SessionLocal() as db:
        exp = db.query(ExperimentResult).filter(
            ExperimentResult.name.like("%logistic_regression%")
        ).order_by(ExperimentResult.created_at.desc()).first()

        assert exp is not None, "ExperimentResult not found in database"
        assert abs(float(exp.backtest_pnl) - summary.net_pnl) < 1e-2
        assert abs(float(exp.backtest_roi) - summary.roi) < 1e-2
        assert "initial_bankroll" in exp.strategy_config

    print("test_database_experiment_recording: OK")


if __name__ == "__main__":
    test_chronological_split_and_purge()
    test_execution_as_of_odds_and_zero_future_leakage()
    test_execution_rejections_stale_and_suspension()
    test_bankroll_sizing_exposure_and_drawdown()
    test_end_to_end_backtest_reproducibility()
    test_database_experiment_recording()
    print("ALL WALK-FORWARD BACKTESTER TESTS PASSED!")
