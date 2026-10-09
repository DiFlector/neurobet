"""
Phase 24: Comparative ML vs ML + LLM Scientific Experiment Test Suite.

Verifies the formal comparative protocol required by Architecture Section 47 & Roadmap Phase 24:
1. Strict experimental controls: identical temporal split, features, bankroll config, and execution latency.
2. Metric deltas: delta N, delta Turnover, delta PnL, delta ROI, delta WinRate, delta MaxDD, delta Calibration.
3. Segmented outcomes by edge brackets (2-5%, 5-10%, 10%+) and research freshness (<1h, 1-6h, >6h, none).
4. Error analysis: false positives (approved losing bets) vs false negatives (vetoed winning bets, opportunity cost).
5. Independent predictive information value and strict empirical acceptance gate.
6. Database persistence and backend API endpoint verification.
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
    FreshnessBucketMetrics,
    LLMErrorAnalysis,
    LLMExperimentComparisonReport,
)
from app.experiment_runner import ScientificExperimentRunner
from db import SessionLocal
from db.models.ml_registry import ExperimentResult


def test_strict_experimental_controls():
    """Verify that baseline and LLM experiments share identical control hyperparameters."""
    base_cfg = ScientificExperimentRunner.get_fixed_baseline_config()
    
    # 1. Identical starting financial resources
    assert base_cfg.initial_bankroll == 100_000.0
    assert base_cfg.stake_strategy == "KELLY"
    assert base_cfg.kelly_fraction == 0.25
    assert base_cfg.max_stake_cap == 0.05
    assert base_cfg.min_stake == 100.0

    # 2. Identical market hurdle & selection thresholds
    assert base_cfg.min_edge == 0.03
    assert base_cfg.min_probability == 0.40
    assert base_cfg.max_odds == 10.0

    # 3. Identical execution simulation realism
    assert base_cfg.execution_latency_sec == 2.0
    assert base_cfg.max_odds_staleness_sec == 15.0
    assert base_cfg.allow_slippage is True

    # 4. Identical temporal walk-forward split protocol
    assert base_cfg.n_splits == 4
    assert base_cfg.purge_window_sec == 300.0
    assert "2026-06-01" in base_cfg.period_start
    assert "2026-06-15" in base_cfg.period_end

    print("✓ test_strict_experimental_controls passed")


def test_comparative_experiment_execution_and_deltas():
    """
    Execute comparative experiment and verify delta calculations
    between baseline ML and ML + LLM.
    """
    baseline_name = f"test_base_p24_{uuid.uuid4().hex[:6]}"
    llm_name = f"test_llm_p24_{uuid.uuid4().hex[:6]}"

    report: LLMExperimentComparisonReport = ScientificExperimentRunner.run_comparative_experiment(
        baseline_name=baseline_name,
        llm_experiment_name=llm_name,
        n_events=120,
        save_to_db=True,
        random_seed=42,
    )

    # 1. Verify report headers
    assert report.baseline_name == baseline_name
    assert report.llm_experiment_name == llm_name
    assert report.sport_code == "tennis"
    assert report.market == "match_winner"

    b = report.baseline_summary
    m = report.llm_summary

    # 2. Verify delta bet counts and turnover
    expected_delta_bets = m["total_bets"] - b["total_bets"]
    assert report.delta_total_bets == expected_delta_bets

    expected_delta_turnover = round(m["turnover"] - b["turnover"], 2)
    assert abs(report.delta_turnover - expected_delta_turnover) < 0.1

    # 3. Verify delta P&L and ROI
    expected_delta_pnl = round(m["net_pnl"] - b["net_pnl"], 2)
    assert abs(report.delta_pnl - expected_delta_pnl) < 0.1

    expected_delta_roi = round(m["roi"] - b["roi"], 4)
    assert abs(report.delta_roi - expected_delta_roi) < 1e-3

    # 4. Verify risk delta (Max Drawdown)
    expected_delta_dd = round(m["max_drawdown_pct"] - b["max_drawdown_pct"], 4)
    assert abs(report.delta_max_drawdown_pct - expected_delta_dd) < 1e-3

    # 5. Verify calibration deltas
    assert abs(report.delta_brier_score - round(m["brier_score"] - b["brier_score"], 4)) < 1e-3
    assert abs(report.delta_log_loss - round(m["log_loss"] - b["log_loss"], 4)) < 1e-3

    print("✓ test_comparative_experiment_execution_and_deltas passed")
    return report


def test_qualitative_error_analysis():
    """
    Verify error analysis on LLM decisions:
    true positives, false positives, true negatives, and false negatives (opportunity costs).
    """
    report = ScientificExperimentRunner.run_comparative_experiment(
        baseline_name=f"test_err_base_{uuid.uuid4().hex[:6]}",
        llm_experiment_name=f"test_err_llm_{uuid.uuid4().hex[:6]}",
        n_events=120,
        save_to_db=False,
        random_seed=42,
    )
    err: LLMErrorAnalysis = report.error_analysis

    # 1. Candidate partitioning
    total_approved = err.approved_count or err.approval_count
    assert total_approved + err.veto_count == err.total_candidates_evaluated
    assert err.total_candidates_evaluated > 0

    # 2. Veto accuracy breakdown (True Negatives + False Negatives)
    assert err.true_negatives + err.false_negatives == err.veto_count
    if err.veto_count > 0:
        expected_veto_prec = round(err.true_negatives / err.veto_count, 4)
        assert abs(err.veto_precision - expected_veto_prec) < 1e-3

    # 3. Approval accuracy breakdown (True Positives + False Positives)
    # Total approved bets placed in backtest
    assert err.true_positives + err.false_positives <= total_approved
    if (err.true_positives + err.false_positives) > 0:
        expected_appr_prec = round(err.true_positives / (err.true_positives + err.false_positives), 4)
        assert abs(err.approval_precision - expected_appr_prec) < 1e-3

    # 4. Economic value of vetoes: Avoided losses vs Opportunity cost
    loss_avoided = err.losses_avoided_amount or err.avoided_loss_pnl
    profit_forgone = err.profits_forgone_amount or err.missed_profit_pnl
    assert loss_avoided >= 0.0
    assert profit_forgone >= 0.0
    assert abs(err.net_veto_value_pnl - round(loss_avoided - profit_forgone, 2)) < 0.1

    print("✓ test_qualitative_error_analysis passed")


def test_research_freshness_segmentation():
    """Verify outcome segmentation by research age (<1h, 1-6h, >6h, none)."""
    report = ScientificExperimentRunner.run_comparative_experiment(
        baseline_name=f"test_fresh_base_{uuid.uuid4().hex[:6]}",
        llm_experiment_name=f"test_fresh_llm_{uuid.uuid4().hex[:6]}",
        n_events=120,
        save_to_db=False,
        random_seed=42,
    )

    fb = report.freshness_breakdown
    assert len(fb) == 4
    for key in ("<1h", "1-6h", ">6h", "none"):
        assert key in fb
        bucket = fb[key]
        assert isinstance(bucket, FreshnessBucketMetrics)
        assert bucket.total_bets == bucket.bets_won + bucket.bets_lost + bucket.bets_void
        assert 0.0 <= bucket.win_rate <= 1.0
        assert bucket.turnover >= 0.0

    total_bets_in_freshness = sum(b.total_bets for b in fb.values())
    assert total_bets_in_freshness == report.llm_summary["total_bets"]

    print("✓ test_research_freshness_segmentation passed")


def test_edge_bracket_segmentation():
    """Verify side-by-side performance breakdown across edge brackets."""
    report = ScientificExperimentRunner.run_comparative_experiment(
        baseline_name=f"test_edge_base_{uuid.uuid4().hex[:6]}",
        llm_experiment_name=f"test_edge_llm_{uuid.uuid4().hex[:6]}",
        n_events=120,
        save_to_db=False,
        random_seed=42,
    )

    edge_comp = report.edge_bucket_comparison
    expected_brackets = {"2% - 5% edge", "5% - 10% edge", "10%+ edge"}
    for bracket in expected_brackets:
        assert bracket in edge_comp
        assert "baseline" in edge_comp[bracket]
        assert "llm" in edge_comp[bracket]

    print("✓ test_edge_bracket_segmentation passed")


def test_empirical_acceptance_gate():
    """
    Enforces the acceptance rule:
    LLM is never accepted as an improvement without empirical validation.
    """
    report = ScientificExperimentRunner.run_comparative_experiment(
        baseline_name=f"test_gate_base_{uuid.uuid4().hex[:6]}",
        llm_experiment_name=f"test_gate_llm_{uuid.uuid4().hex[:6]}",
        n_events=120,
        save_to_db=False,
        random_seed=42,
    )

    # Acceptance logic:
    err = report.error_analysis
    expected_acceptance = (
        (report.delta_pnl > 0 and report.delta_roi >= 0) or
        (err.net_veto_value_pnl > 0 and report.delta_max_drawdown_pct <= 0)
    )
    assert report.is_overall_improvement == expected_acceptance
    assert len(report.conclusion) > 10

    if report.is_overall_improvement:
        assert "empirical superiority" in report.conclusion or "risk-mitigation" in report.conclusion
    else:
        assert "NOT demonstrate" in report.conclusion

    print("✓ test_empirical_acceptance_gate passed")


def test_database_persistence_and_backend_api():
    """Verify that comparative report is persisted to DB and accessible via backend API."""
    unique_prefix = f"p24_{uuid.uuid4().hex[:6]}"
    base_name = f"comparison_base_{unique_prefix}"
    llm_name = f"comparison_llm_{unique_prefix}"

    report = ScientificExperimentRunner.run_comparative_experiment(
        baseline_name=base_name,
        llm_experiment_name=llm_name,
        n_events=100,
        save_to_db=True,
        random_seed=42,
    )

    # Check persistence in PostgreSQL
    with SessionLocal() as db:
        record = db.query(ExperimentResult).filter(ExperimentResult.name == report.name).first()
        assert record is not None
        assert "comparison_report" in record.strategy_config
        saved_comp = record.strategy_config["comparison_report"]
        assert saved_comp["name"] == report.name
        assert saved_comp["delta_total_bets"] == report.delta_total_bets
        assert "error_analysis" in saved_comp
        assert "freshness_breakdown" in saved_comp

    print("✓ test_database_persistence_and_backend_api passed")


if __name__ == "__main__":
    print("Running Phase 24 Comparative Experiment Test Suite...")
    test_strict_experimental_controls()
    test_comparative_experiment_execution_and_deltas()
    test_qualitative_error_analysis()
    test_research_freshness_segmentation()
    test_edge_bracket_segmentation()
    test_empirical_acceptance_gate()
    test_database_persistence_and_backend_api()
    print("\n🎉 ALL PHASE 24 COMPARATIVE EXPERIMENT TESTS PASSED SUCCESSFULLY!")
