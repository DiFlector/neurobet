"""
Phase 23: Scientific Baseline Experiment Test Suite.

Verifies the fixed experimental baseline required by Architecture Section 47 & Roadmap Phase 23:
1. Fixed initial bankroll, period, sport, market, min edge, and stake policy.
2. ML-only execution (LLM disabled).
3. Log Loss and Brier Score statistical evaluation.
4. ROI, P&L, Turnover, and Win Rate calculations.
5. Maximum Drawdown calculation.
6. 10-bin Reliability / Calibration Curve generation.
7. Database persistence and backend API verification.
"""

import sys
import uuid
from datetime import datetime, timezone

# Ensure all package source paths are discoverable in container and host
paths = [
    "/srv/neurobet/packages/contracts/src",
    "/srv/neurobet/packages/features/src",
    "/srv/neurobet/packages/db/src",
    "/srv/neurobet/packages/sports-core/src",
    "/srv/neurobet/packages/bankroll/src",
    "/srv/neurobet/services/neural",
    "/srv/neurobet/services/backend",
    "/app/packages/contracts/src",
    "/app/packages/features/src",
    "/app/packages/db/src",
    "/app/packages/sports-core/src",
    "/app/packages/bankroll/src",
    "/app",
]
for p in paths:
    if p not in sys.path:
        sys.path.insert(0, p)

from contracts import (
    ScientificExperimentConfig,
    ScientificExperimentResultContract,
)
from app.experiment_runner import ScientificExperimentRunner
from db import SessionLocal
from db.models.ml_registry import ExperimentResult


def test_fixed_experimental_configuration():
    """Verify that all baseline experimental hyperparameters are strictly fixed."""
    cfg = ScientificExperimentRunner.get_fixed_baseline_config()

    # 1. Initial bankroll fixed at 100,000 RUB
    assert cfg.initial_bankroll == 100_000.0
    # 2. Sport fixed to tennis
    assert cfg.sport_code == "tennis"
    # 3. Market fixed to match_winner
    assert cfg.market == "match_winner"
    # 4. Period fixed
    assert "2026-06-01" in cfg.period_start
    assert "2026-06-15" in cfg.period_end
    # 5. Minimum edge fixed to 3%
    assert cfg.min_edge == 0.03
    # 6. Stake policy fixed to Quarter Kelly with 5% cap
    assert cfg.stake_strategy == "KELLY"
    assert cfg.kelly_fraction == 0.25
    assert cfg.max_stake_cap == 0.05
    assert cfg.min_stake == 100.0
    # 7. ML-only mode (LLM disabled)
    assert cfg.use_llm is False
    assert cfg.model_type in ("gradient_boosting", "logistic_regression")

    print("✓ test_fixed_experimental_configuration passed")


def test_ml_only_baseline_experiment_execution():
    """
    Execute baseline ML-only walk-forward backtest experiment
    and verify statistical, financial, risk, and calibration metrics.
    """
    exp_name = f"test_baseline_experiment_{uuid.uuid4().hex[:8]}"
    cfg = ScientificExperimentRunner.get_fixed_baseline_config(experiment_name=exp_name)

    res: ScientificExperimentResultContract = ScientificExperimentRunner.run_baseline_ml_experiment(
        config=cfg,
        n_events=120,
        save_to_db=True,
    )

    # 1. Verify basic contract integrity
    assert res.name == exp_name
    assert res.config.sport_code == "tennis"
    assert res.initial_bankroll == 100_000.0
    assert res.final_bankroll > 0.0

    # 2. Verify statistical metrics: Log Loss and Brier Score
    assert 0.0 < res.log_loss < 3.0, f"Log loss out of bounds: {res.log_loss}"
    assert 0.0 < res.brier_score <= 1.0, f"Brier score out of bounds: {res.brier_score}"
    assert 0.0 <= res.roc_auc <= 1.0, f"ROC AUC out of bounds: {res.roc_auc}"
    assert 0.0 <= res.expected_calibration_error <= 1.0, f"ECE out of bounds: {res.expected_calibration_error}"

    # 3. Verify financial metrics: ROI and P&L
    assert res.turnover > 0.0
    assert res.total_bets == res.bets_won + res.bets_lost + res.bets_void
    assert res.total_bets > 0
    expected_pnl = round(res.final_bankroll - res.initial_bankroll, 2)
    assert abs(res.net_pnl - expected_pnl) < 1.0  # Accounts for minor float rounding
    assert abs(res.roi - (res.net_pnl / res.turnover)) < 1e-3

    # 4. Verify risk metrics: Max Drawdown
    assert res.max_drawdown_amount >= 0.0
    assert 0.0 <= res.max_drawdown_pct <= 1.0
    assert res.peak_bankroll >= res.initial_bankroll
    assert res.min_bankroll <= res.final_bankroll or res.min_bankroll <= res.initial_bankroll

    # 5. Verify 10-bin Calibration Curve
    assert res.calibration_curve.n_bins == 10
    assert len(res.calibration_curve.points) == 10
    total_samples_in_bins = sum(p.sample_count for p in res.calibration_curve.points)
    assert total_samples_in_bins > 0

    for idx, pt in enumerate(res.calibration_curve.points):
        assert pt.bin_index == idx
        assert 0.0 <= pt.mean_predicted_prob <= 1.0
        assert 0.0 <= pt.fraction_positives <= 1.0
        assert pt.sample_count >= 0

    # 6. Verify Odds and Edge buckets
    assert len(res.odds_buckets) > 0
    assert len(res.edge_buckets) > 0

    print("✓ test_ml_only_baseline_experiment_execution passed")
    return res


def test_database_persistence_and_retrieval():
    """Verify that experiment result is saved to PostgreSQL and readable."""
    exp_name = f"test_db_persist_{uuid.uuid4().hex[:8]}"
    cfg = ScientificExperimentRunner.get_fixed_baseline_config(experiment_name=exp_name)

    res = ScientificExperimentRunner.run_baseline_ml_experiment(
        config=cfg,
        n_events=80,
        save_to_db=True,
    )

    with SessionLocal() as db:
        saved = db.query(ExperimentResult).filter(ExperimentResult.name == exp_name).first()
        assert saved is not None, f"ExperimentResult '{exp_name}' not found in database!"
        assert float(saved.backtest_pnl) == float(res.net_pnl)
        assert float(saved.backtest_roi) == float(res.roi)
        assert float(saved.win_rate) == float(res.win_rate)
        assert float(saved.max_drawdown) == float(res.max_drawdown_pct)

        # Check strategy_config JSON details
        strat = saved.strategy_config
        assert "config" in strat
        assert "statistical_metrics" in strat
        assert "calibration_curve" in strat
        assert strat["statistical_metrics"]["log_loss"] == res.log_loss
        assert strat["statistical_metrics"]["brier_score"] == res.brier_score
        assert strat["calibration_curve"]["n_bins"] == 10

    print("✓ test_database_persistence_and_retrieval passed")


if __name__ == "__main__":
    print("Running Phase 23 Scientific Baseline Experiment Test Suite...")
    test_fixed_experimental_configuration()
    test_ml_only_baseline_experiment_execution()
    test_database_persistence_and_retrieval()
    print("\n🎉 ALL PHASE 23 SCIENTIFIC BASELINE EXPERIMENT TESTS PASSED SUCCESSFULLY!")
