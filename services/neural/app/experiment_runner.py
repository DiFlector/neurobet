"""
Scientific Baseline Experiment Runner for Phase 23.

Implements the experimental protocol required by Architecture Section 47:
Fixes initial bankroll, evaluation period, sport, markets, min edge, and stake policy,
executes ML-only walk-forward backtest, computes statistical, calibration, and financial
metrics, and persists the result to PostgreSQL experiment_results.
"""

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
import numpy as np

from contracts import (
    ScientificExperimentConfig,
    ScientificExperimentResultContract,
    CalibrationPoint,
    CalibrationCurveReport,
    FreshnessBucketMetrics,
    LLMErrorAnalysis,
    LLMExperimentComparisonReport,
)
from db import SessionLocal
from db.models.ml_registry import ExperimentResult
from .backtest.config import BacktestConfig
from .backtest.engine import WalkForwardBacktester, generate_synthetic_backtest_samples, BacktestSample
from .backtest.metrics import BacktestSummary

logger = logging.getLogger("neural.experiment")


class ScientificExperimentRunner:
    """
    Executes reproducible scientific experiments comparing ML and LLM performance.
    """

    @classmethod
    def get_fixed_baseline_config(
        cls,
        experiment_name: str = "baseline_ml_only_tennis_h2h_2026",
        random_seed: int = 42,
    ) -> ScientificExperimentConfig:
        """
        Returns the strictly fixed Phase 23 baseline experiment configuration.
        """
        period_start = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc).isoformat()
        period_end = datetime(2026, 6, 15, 22, 0, 0, tzinfo=timezone.utc).isoformat()

        return ScientificExperimentConfig(
            experiment_name=experiment_name,
            sport_code="tennis",
            market="match_winner",
            period_start=period_start,
            period_end=period_end,
            initial_bankroll=100000.0,
            stake_strategy="KELLY",
            kelly_fraction=0.25,
            max_stake_cap=0.05,
            min_stake=100.0,
            min_edge=0.03,
            min_probability=0.40,
            max_odds=10.0,
            execution_latency_sec=2.0,
            max_odds_staleness_sec=15.0,
            allow_slippage=True,
            model_type="gradient_boosting",
            use_llm=False,
            n_splits=4,
            purge_window_sec=300.0,
            random_seed=random_seed,
        )

    @classmethod
    def run_baseline_ml_experiment(
        cls,
        config: Optional[ScientificExperimentConfig] = None,
        samples: Optional[list] = None,
        n_events: int = 160,
        save_to_db: bool = True,
    ) -> ScientificExperimentResultContract:
        """
        Executes the fixed ML-only baseline experiment.
        """
        exp_cfg = config or cls.get_fixed_baseline_config()
        logger.info(
            f"Starting Scientific Baseline Experiment '{exp_cfg.experiment_name}' "
            f"on {exp_cfg.sport_code.upper()} ({exp_cfg.market}) | Initial Bankroll: {exp_cfg.initial_bankroll} RUB"
        )

        bt_cfg = BacktestConfig(
            sport_code=exp_cfg.sport_code,
            market=exp_cfg.market,
            experiment_name=exp_cfg.experiment_name,
            period_start=exp_cfg.period_start,
            period_end=exp_cfg.period_end,
            use_llm=exp_cfg.use_llm,
            initial_bankroll=exp_cfg.initial_bankroll,
            stake_strategy=exp_cfg.stake_strategy,
            kelly_fraction=exp_cfg.kelly_fraction,
            max_stake_cap=exp_cfg.max_stake_cap,
            min_stake=exp_cfg.min_stake,
            min_edge=exp_cfg.min_edge,
            min_probability=exp_cfg.min_probability,
            max_odds=exp_cfg.max_odds,
            execution_latency_sec=exp_cfg.execution_latency_sec,
            max_odds_staleness_sec=exp_cfg.max_odds_staleness_sec,
            allow_slippage=exp_cfg.allow_slippage,
            n_splits=exp_cfg.n_splits,
            purge_window_sec=exp_cfg.purge_window_sec,
            random_seed=exp_cfg.random_seed,
        )

        backtester = WalkForwardBacktester(config=bt_cfg)

        if samples is None:
            samples = generate_synthetic_backtest_samples(
                n_events=n_events,
                random_seed=exp_cfg.random_seed,
            )

        summary: BacktestSummary = backtester.run(
            samples=samples,
            model_type=exp_cfg.model_type,
            save_to_db=save_to_db,
        )

        calib_points = [
            CalibrationPoint(
                bin_index=p["bin_index"],
                bin_range=p["bin_range"],
                mean_predicted_prob=p["mean_predicted_prob"],
                fraction_positives=p["fraction_positives"],
                sample_count=p["sample_count"],
            )
            for p in summary.calibration_curve.get("points", [])
        ]

        calib_report = CalibrationCurveReport(
            n_bins=summary.calibration_curve.get("n_bins", 10),
            points=calib_points,
            expected_calibration_error=summary.expected_calibration_error,
            brier_score=summary.brier_score,
            log_loss=summary.log_loss,
        )

        freshness_buckets_contract = {
            k: FreshnessBucketMetrics(
                bucket_name=v.bucket_name,
                total_bets=v.total_bets,
                bets_won=v.bets_won,
                bets_lost=v.bets_lost,
                bets_void=v.bets_void,
                win_rate=v.win_rate,
                turnover=v.turnover,
                net_pnl=v.net_pnl,
                roi=v.roi,
                avg_odds=v.avg_odds,
                avg_edge=v.avg_edge,
            )
            for k, v in summary.freshness_buckets.items()
        }

        result_contract = ScientificExperimentResultContract(
            name=exp_cfg.experiment_name,
            config=exp_cfg,
            initial_bankroll=summary.initial_bankroll,
            final_bankroll=summary.final_bankroll,
            net_pnl=summary.net_pnl,
            roi=summary.roi,
            turnover=summary.turnover,
            win_rate=summary.win_rate,
            total_bets=summary.total_bets,
            bets_won=summary.bets_won,
            bets_lost=summary.bets_lost,
            bets_void=summary.bets_void,
            peak_bankroll=summary.peak_bankroll,
            min_bankroll=summary.min_bankroll,
            peak_exposure=summary.peak_exposure,
            max_drawdown_amount=summary.max_drawdown_amount,
            max_drawdown_pct=summary.max_drawdown_pct,
            log_loss=summary.log_loss,
            brier_score=summary.brier_score,
            roc_auc=summary.roc_auc,
            expected_calibration_error=summary.expected_calibration_error,
            calibration_curve=calib_report,
            odds_buckets={k: v.__dict__ for k, v in summary.odds_buckets.items()},
            edge_buckets={k: v.__dict__ for k, v in summary.edge_buckets.items()},
            freshness_buckets=freshness_buckets_contract,
            created_at=datetime.now(timezone.utc),
        )

        logger.info(
            f"Experiment '{exp_cfg.experiment_name}' Complete! "
            f"P&L: {result_contract.net_pnl:+.2f} RUB | ROI: {result_contract.roi*100:.2f}% | "
            f"LogLoss: {result_contract.log_loss:.4f} | Brier: {result_contract.brier_score:.4f} | "
            f"MaxDD: {result_contract.max_drawdown_pct*100:.2f}%"
        )
        return result_contract

    @classmethod
    def run_comparative_experiment(
        cls,
        baseline_name: str = "baseline_ml_only_tennis_h2h_2026",
        llm_experiment_name: str = "ml_llm_tennis_h2h_2026",
        n_events: int = 160,
        save_to_db: bool = True,
        random_seed: int = 42,
    ) -> LLMExperimentComparisonReport:
        """
        Executes a controlled comparative experiment:
        Runs ML-only baseline and ML+LLM on identical splits, bankroll, and latency parameters.
        Quantifies all metric deltas, edge buckets, research freshness, error analysis,
        and enforces the empirical acceptance gate.
        """
        logger.info("=================================================================")
        logger.info("Starting Phase 24 Comparative Experiment: ML vs ML + LLM")
        logger.info("=================================================================")

        # 1. Deterministic generation of test samples once for strict experimental parity
        samples = generate_synthetic_backtest_samples(
            n_events=n_events,
            random_seed=random_seed,
        )

        # 2. Run Baseline ML (use_llm=False)
        base_cfg = cls.get_fixed_baseline_config(
            experiment_name=baseline_name,
            random_seed=random_seed,
        )
        base_res = cls.run_baseline_ml_experiment(
            config=base_cfg,
            samples=samples,
            save_to_db=save_to_db,
        )

        # 3. Run ML + LLM (use_llm=True) with strictly identical control parameters
        llm_cfg = ScientificExperimentConfig(
            experiment_name=llm_experiment_name,
            sport_code=base_cfg.sport_code,
            market=base_cfg.market,
            period_start=base_cfg.period_start,
            period_end=base_cfg.period_end,
            initial_bankroll=base_cfg.initial_bankroll,
            stake_strategy=base_cfg.stake_strategy,
            kelly_fraction=base_cfg.kelly_fraction,
            max_stake_cap=base_cfg.max_stake_cap,
            min_stake=base_cfg.min_stake,
            min_edge=base_cfg.min_edge,
            min_probability=base_cfg.min_probability,
            max_odds=base_cfg.max_odds,
            execution_latency_sec=base_cfg.execution_latency_sec,
            max_odds_staleness_sec=base_cfg.max_odds_staleness_sec,
            allow_slippage=base_cfg.allow_slippage,
            model_type=base_cfg.model_type,
            use_llm=True,
            n_splits=base_cfg.n_splits,
            purge_window_sec=base_cfg.purge_window_sec,
            random_seed=random_seed,
        )

        # Backtester for LLM
        bt_cfg_llm = BacktestConfig(
            sport_code=llm_cfg.sport_code,
            market=llm_cfg.market,
            experiment_name=llm_cfg.experiment_name,
            period_start=llm_cfg.period_start,
            period_end=llm_cfg.period_end,
            use_llm=True,
            initial_bankroll=llm_cfg.initial_bankroll,
            stake_strategy=llm_cfg.stake_strategy,
            kelly_fraction=llm_cfg.kelly_fraction,
            max_stake_cap=llm_cfg.max_stake_cap,
            min_stake=llm_cfg.min_stake,
            min_edge=llm_cfg.min_edge,
            min_probability=llm_cfg.min_probability,
            max_odds=llm_cfg.max_odds,
            execution_latency_sec=llm_cfg.execution_latency_sec,
            max_odds_staleness_sec=llm_cfg.max_odds_staleness_sec,
            allow_slippage=llm_cfg.allow_slippage,
            n_splits=llm_cfg.n_splits,
            purge_window_sec=llm_cfg.purge_window_sec,
            random_seed=llm_cfg.random_seed,
        )
        backtester_llm = WalkForwardBacktester(config=bt_cfg_llm)
        llm_summary: BacktestSummary = backtester_llm.run(
            samples=samples,
            model_type=llm_cfg.model_type,
            save_to_db=save_to_db,
        )

        calib_points_llm = [
            CalibrationPoint(
                bin_index=p["bin_index"],
                bin_range=p["bin_range"],
                mean_predicted_prob=p["mean_predicted_prob"],
                fraction_positives=p["fraction_positives"],
                sample_count=p["sample_count"],
            )
            for p in llm_summary.calibration_curve.get("points", [])
        ]
        calib_report_llm = CalibrationCurveReport(
            n_bins=llm_summary.calibration_curve.get("n_bins", 10),
            points=calib_points_llm,
            expected_calibration_error=llm_summary.expected_calibration_error,
            brier_score=llm_summary.brier_score,
            log_loss=llm_summary.log_loss,
        )

        freshness_buckets_contract = {
            k: FreshnessBucketMetrics(
                bucket_name=v.bucket_name,
                total_bets=v.total_bets,
                bets_won=v.bets_won,
                bets_lost=v.bets_lost,
                bets_void=v.bets_void,
                win_rate=v.win_rate,
                turnover=v.turnover,
                net_pnl=v.net_pnl,
                roi=v.roi,
                avg_odds=v.avg_odds,
                avg_edge=v.avg_edge,
            )
            for k, v in llm_summary.freshness_buckets.items()
        }

        llm_res = ScientificExperimentResultContract(
            name=llm_cfg.experiment_name,
            config=llm_cfg,
            initial_bankroll=llm_summary.initial_bankroll,
            final_bankroll=llm_summary.final_bankroll,
            net_pnl=llm_summary.net_pnl,
            roi=llm_summary.roi,
            turnover=llm_summary.turnover,
            win_rate=llm_summary.win_rate,
            total_bets=llm_summary.total_bets,
            bets_won=llm_summary.bets_won,
            bets_lost=llm_summary.bets_lost,
            bets_void=llm_summary.bets_void,
            peak_bankroll=llm_summary.peak_bankroll,
            min_bankroll=llm_summary.min_bankroll,
            peak_exposure=llm_summary.peak_exposure,
            max_drawdown_amount=llm_summary.max_drawdown_amount,
            max_drawdown_pct=llm_summary.max_drawdown_pct,
            log_loss=llm_summary.log_loss,
            brier_score=llm_summary.brier_score,
            roc_auc=llm_summary.roc_auc,
            expected_calibration_error=llm_summary.expected_calibration_error,
            calibration_curve=calib_report_llm,
            odds_buckets={k: v.__dict__ for k, v in llm_summary.odds_buckets.items()},
            edge_buckets={k: v.__dict__ for k, v in llm_summary.edge_buckets.items()},
            freshness_buckets=freshness_buckets_contract,
            created_at=datetime.now(timezone.utc),
        )

        # 4. Compute Deltas (LLM - Baseline)
        delta_total_bets = llm_res.total_bets - base_res.total_bets
        delta_turnover = round(llm_res.turnover - base_res.turnover, 2)
        delta_net_pnl = round(llm_res.net_pnl - base_res.net_pnl, 2)
        delta_roi = round(llm_res.roi - base_res.roi, 4)
        delta_win_rate = round(llm_res.win_rate - base_res.win_rate, 4)
        delta_max_drawdown_pct = round(llm_res.max_drawdown_pct - base_res.max_drawdown_pct, 4)
        delta_log_loss = round(llm_res.log_loss - base_res.log_loss, 4)
        delta_brier_score = round(llm_res.brier_score - base_res.brier_score, 4)
        delta_expected_calibration_error = round(
            llm_res.expected_calibration_error - base_res.expected_calibration_error, 4
        )

        # 5. Extract Error Analysis
        err_raw = llm_summary.error_analysis or {}
        error_analysis = LLMErrorAnalysis(
            total_candidates_evaluated=err_raw.get("total_candidates_evaluated", 0),
            veto_count=err_raw.get("veto_count", 0),
            approval_count=err_raw.get("approval_count", 0),
            true_positives=err_raw.get("true_positives", 0),
            false_positives=err_raw.get("false_positives", 0),
            true_negatives=err_raw.get("true_negatives", 0),
            false_negatives=err_raw.get("false_negatives", 0),
            veto_precision=err_raw.get("veto_precision", 0.0),
            approval_precision=err_raw.get("approval_precision", 0.0),
            avoided_loss_pnl=err_raw.get("avoided_loss_pnl", 0.0),
            missed_profit_pnl=err_raw.get("missed_profit_pnl", 0.0),
            net_veto_value_pnl=err_raw.get("net_veto_value_pnl", 0.0),
        )

        # 6. Empirical Acceptance Gate
        # Acceptance condition: LLM must demonstrate positive empirical advantage
        # (either higher PnL and non-negative ROI delta, OR positive net veto value with non-increasing drawdown).
        is_overall_improvement = (
            (delta_net_pnl > 0 and delta_roi >= 0) or
            (error_analysis.net_veto_value_pnl > 0 and delta_max_drawdown_pct <= 0)
        )

        if is_overall_improvement:
            improvement_justification = (
                f"LLM demonstrated hard empirical superiority: P&L delta {delta_net_pnl:+.2f} RUB, "
                f"ROI delta {delta_roi*100:+.2f}%, and net veto value {error_analysis.net_veto_value_pnl:+.2f} RUB "
                f"with drawdown delta {delta_max_drawdown_pct*100:+.2f}%."
            )
        else:
            improvement_justification = (
                f"LLM did NOT demonstrate empirical superiority under current parameters: "
                f"ΔPnL: {delta_net_pnl:+.2f} RUB, ΔROI: {delta_roi*100:+.2f}%, net veto value: {error_analysis.net_veto_value_pnl:+.2f} RUB."
            )

        report = LLMExperimentComparisonReport(
            name=f"comparison_{baseline_name}_vs_{llm_experiment_name}",
            baseline_name=baseline_name,
            llm_experiment_name=llm_experiment_name,
            sport_code=base_cfg.sport_code,
            market=base_cfg.market,
            period_start=base_cfg.period_start,
            period_end=base_cfg.period_end,
            baseline_summary=base_res.model_dump(mode="json"),
            llm_summary=llm_res.model_dump(mode="json"),
            delta_total_bets=delta_total_bets,
            delta_turnover=delta_turnover,
            delta_pnl=delta_net_pnl,
            delta_roi=delta_roi,
            delta_win_rate=delta_win_rate,
            delta_max_drawdown_amount=round(llm_res.max_drawdown_amount - base_res.max_drawdown_amount, 2),
            delta_max_drawdown_pct=delta_max_drawdown_pct,
            delta_log_loss=delta_log_loss,
            delta_brier_score=delta_brier_score,
            delta_ece=delta_expected_calibration_error,
            edge_bucket_comparison={
                k: {"baseline": base_res.edge_buckets.get(k, {}), "llm": llm_res.edge_buckets.get(k, {})}
                for k in set(base_res.edge_buckets.keys()).union(llm_res.edge_buckets.keys())
            },
            freshness_breakdown=freshness_buckets_contract,
            error_analysis=error_analysis,
            has_independent_information=True,
            is_overall_improvement=is_overall_improvement,
            conclusion=improvement_justification,
            created_at=datetime.now(timezone.utc),
        )

        # 7. Persist comparison report in PostgreSQL if requested
        if save_to_db:
            try:
                with SessionLocal() as db:
                    strat_dict = {
                        "comparison_report": report.model_dump(mode="json"),
                        "baseline_name": baseline_name,
                        "llm_experiment_name": llm_experiment_name,
                        "is_overall_improvement": is_overall_improvement,
                        "deltas": {
                            "delta_total_bets": delta_total_bets,
                            "delta_net_pnl": delta_net_pnl,
                            "delta_roi": delta_roi,
                            "delta_max_drawdown_pct": delta_max_drawdown_pct,
                            "delta_brier_score": delta_brier_score,
                        },
                        "error_analysis": error_analysis.model_dump(mode="json"),
                    }
                    exp = ExperimentResult(
                        name=report.name,
                        strategy_config=strat_dict,
                        backtest_pnl=float(delta_net_pnl),
                        backtest_roi=float(delta_roi),
                        win_rate=float(delta_win_rate),
                        max_drawdown=float(delta_max_drawdown_pct),
                    )
                    db.add(exp)
                    db.commit()
                    logger.info(f"Saved LLMExperimentComparisonReport to database with ID: {exp.id}")
            except Exception as e:
                logger.warning(f"Could not persist LLMExperimentComparisonReport to database: {e}")

        return report


def print_experiment_report(res: ScientificExperimentResultContract) -> None:
    """Print ASCII summary of the scientific experiment."""
    print("\n" + "=" * 76)
    print(" 🔬 NEUROBET SCIENTIFIC BASELINE EXPERIMENT REPORT (PHASE 23)")
    print("=" * 76)
    print(f" Experiment Name:    {res.name}")
    print(f" Sport & Market:     {res.config.sport_code.upper()} — {res.config.market}")
    print(f" Evaluation Period:  {res.config.period_start} → {res.config.period_end}")
    print(f" Model Architecture: {res.config.model_type.upper()} (LLM: {'ENABLED' if res.config.use_llm else 'DISABLED (ML-only)'})")
    print(f" Staking Policy:     {res.config.stake_strategy} (Kelly Fraction: {res.config.kelly_fraction})")
    print(f" Minimum Edge:       +{res.config.min_edge * 100:.1f}%")
    print("-" * 76)
    print(f" Initial Bankroll:   {res.initial_bankroll:,.2f} RUB")
    print(f" Final Bankroll:     {res.final_bankroll:,.2f} RUB")
    pnl_sign = "+" if res.net_pnl >= 0 else ""
    print(f" Net P&L:            {pnl_sign}{res.net_pnl:,.2f} RUB")
    print(f" ROI:                {res.roi * 100:+.2f}%")
    print(f" Turnover:           {res.turnover:,.2f} RUB")
    print(f" Win Rate:           {res.win_rate * 100:.2f}%")
    print(f" Total Bets Placed:  {res.total_bets} (Won: {res.bets_won}, Lost: {res.bets_lost}, Void: {res.bets_void})")
    print(f" Max Drawdown:       {res.max_drawdown_pct * 100:.2f}% ({res.max_drawdown_amount:,.2f} RUB)")
    print("-" * 76)
    print(" 🎯 PROBABILISTIC & STATISTICAL METRICS:")
    print(f" Log Loss (Entropy): {res.log_loss:.4f}")
    print(f" Brier Score:        {res.brier_score:.4f}")
    print(f" ROC AUC Score:      {res.roc_auc:.4f}")
    print(f" Calibration (ECE):  {res.expected_calibration_error:.4f}")
    print("-" * 76)
    print(" 📐 RELIABILITY / CALIBRATION CURVE (10 BINS):")
    print(f" {'Bin Range':<12} | {'Mean Pred Prob':<16} | {'Empirical Win Rate':<20} | {'Sample Count':<12}")
    print("-" * 76)
    for p in res.calibration_curve.points:
        print(f" {p.bin_range:<12} | {p.mean_predicted_prob:>14.4f} | {p.fraction_positives:>18.4f} | {p.sample_count:>12}")
    print("=" * 76 + "\n")


def print_comparison_report(rep: LLMExperimentComparisonReport) -> None:
    """Print comprehensive ASCII comparison report between ML Baseline and ML+LLM."""
    print("\n" + "=" * 84)
    print(" 🔬 NEUROBET PHASE 24: SCIENTIFIC COMPARISON REPORT (ML vs ML + LLM)")
    print("=" * 84)
    print(f" Comparison Run:     {rep.name}")
    print(f" Sport & Market:     {rep.sport_code.upper()} — {rep.market}")
    print(f" Evaluation Period:  {rep.period_start} → {rep.period_end}")
    print(f" Strict Controls:    Identical splits (4 folds), Kelly (0.25), Latency (2.0s), Seeds")
    print("-" * 84)
    print(f" {'Metric':<26} | {'Baseline (ML-only)':<18} | {'ML + LLM':<18} | {'Delta':<14}")
    print("-" * 84)
    b = rep.baseline_summary
    m = rep.llm_summary
    print(f" {'Total Bets Placed':<26} | {b.get('total_bets', 0):<18} | {m.get('total_bets', 0):<18} | {rep.delta_total_bets:+d}")
    print(f" {'Turnover':<26} | {b.get('turnover', 0.0):>14,.2f} RUB | {m.get('turnover', 0.0):>14,.2f} RUB | {rep.delta_turnover:>+10,.2f} RUB")
    print(f" {'Net P&L':<26} | {b.get('net_pnl', 0.0):>14,.2f} RUB | {m.get('net_pnl', 0.0):>14,.2f} RUB | {rep.delta_pnl:>+10,.2f} RUB")
    print(f" {'ROI':<26} | {b.get('roi', 0.0)*100:>17.2f}% | {m.get('roi', 0.0)*100:>17.2f}% | {rep.delta_roi*100:>+13.2f}%")
    print(f" {'Win Rate':<26} | {b.get('win_rate', 0.0)*100:>17.2f}% | {m.get('win_rate', 0.0)*100:>17.2f}% | {rep.delta_win_rate*100:>+13.2f}%")
    print(f" {'Max Drawdown':<26} | {b.get('max_drawdown_pct', 0.0)*100:>17.2f}% | {m.get('max_drawdown_pct', 0.0)*100:>17.2f}% | {rep.delta_max_drawdown_pct*100:>+13.2f}%")
    print(f" {'Brier Score':<26} | {b.get('brier_score', 0.0):>18.4f} | {m.get('brier_score', 0.0):>18.4f} | {rep.delta_brier_score:>+14.4f}")
    print(f" {'ECE (Calibration)':<26} | {b.get('expected_calibration_error', 0.0):>18.4f} | {m.get('expected_calibration_error', 0.0):>18.4f} | {rep.delta_ece:>+14.4f}")
    print("-" * 84)
    print(" 🎯 QUALITATIVE ERROR ANALYSIS (LLM VETO & APPROVAL PRECISION):")
    err = rep.error_analysis
    print(f"  Candidates Evaluated: {err.total_candidates_evaluated}")
    print(f"  Approved: {err.approved_count or err.approval_count}  |  Vetoed: {err.veto_count}")
    print(f"  True Positives  (Approved & Won):  {err.true_positives:<4} | True Negatives  (Vetoed & Lost): {err.true_negatives:<4}")
    print(f"  False Positives (Approved & Lost): {err.false_positives:<4} | False Negatives (Vetoed & Won):  {err.false_negatives:<4}")
    print(f"  Veto Precision (Saved Loss Rate):  {err.veto_precision*100:.2f}%")
    print(f"  Avoided Losses: +{err.losses_avoided_amount or err.avoided_loss_pnl:,.2f} RUB  |  Opportunity Cost: -{err.profits_forgone_amount or err.missed_profit_pnl:,.2f} RUB")
    print(f"  Net Veto Economic Value:           {err.net_veto_value_pnl:+,.2f} RUB")
    print("-" * 84)
    print(" ⏳ BREAKDOWN BY RESEARCH FRESHNESS:")
    print(f"  {'Freshness':<10} | {'Bets':<6} | {'Won':<6} | {'Lost':<6} | {'Win Rate':<10} | {'Net P&L':<14} | {'ROI':<10}")
    print("  " + "-" * 72)
    for cat, fb in rep.freshness_breakdown.items():
        print(f"  {cat:<10} | {fb.total_bets:<6} | {fb.bets_won:<6} | {fb.bets_lost:<6} | {fb.win_rate*100:>8.1f}% | {fb.net_pnl:>+10,.2f} RUB | {fb.roi*100:>+8.1f}%")
    print("-" * 84)
    print(f" 🏁 EMPIRICAL ACCEPTANCE VERDICT: {'ACCEPTED (SUPERIOR)' if rep.is_overall_improvement else 'REJECTED (INFERIOR)'}")
    print(f"    {rep.conclusion}")
    print("=" * 84 + "\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Neurobet Scientific Baseline & LLM Experiment Runner")
    parser.add_argument("--name", default="baseline_ml_only_tennis_h2h_2026", help="Experiment name")
    parser.add_argument("--compare", action="store_true", help="Run comparative experiment ML vs ML+LLM")
    parser.add_argument("--events", type=int, default=160, help="Number of test events to simulate")
    parser.add_argument("--save-db", action="store_true", default=True, help="Save to experiment_results table")
    args = parser.parse_args()

    if args.compare:
        comparison_report = ScientificExperimentRunner.run_comparative_experiment(
            baseline_name=args.name,
            llm_experiment_name=f"{args.name}_with_llm",
            n_events=args.events,
            save_to_db=args.save_db,
        )
        print_comparison_report(comparison_report)
    else:
        cfg = ScientificExperimentRunner.get_fixed_baseline_config(experiment_name=args.name)
        result = ScientificExperimentRunner.run_baseline_ml_experiment(
            config=cfg,
            n_events=args.events,
            save_to_db=args.save_db,
        )
        print_experiment_report(result)

