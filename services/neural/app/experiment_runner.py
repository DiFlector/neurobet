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
)
from db import SessionLocal
from db.models.ml_registry import ExperimentResult
from .backtest.config import BacktestConfig
from .backtest.engine import WalkForwardBacktester, generate_synthetic_backtest_samples
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

        # Map to internal BacktestConfig
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

        # Generate deterministic event samples for the fixed period
        samples = generate_synthetic_backtest_samples(
            n_events=n_events,
            random_seed=exp_cfg.random_seed,
        )

        # Execute walk-forward simulation
        summary: BacktestSummary = backtester.run(
            samples=samples,
            model_type=exp_cfg.model_type,
            save_to_db=save_to_db,
        )

        # Format calibration points
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
            created_at=datetime.now(timezone.utc),
        )

        logger.info(
            f"Baseline ML Experiment '{exp_cfg.experiment_name}' Complete! "
            f"P&L: {result_contract.net_pnl:+.2f} RUB | ROI: {result_contract.roi*100:.2f}% | "
            f"LogLoss: {result_contract.log_loss:.4f} | Brier: {result_contract.brier_score:.4f} | "
            f"MaxDD: {result_contract.max_drawdown_pct*100:.2f}%"
        )
        return result_contract


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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Neurobet Scientific Baseline Experiment Runner")
    parser.add_argument("--name", default="baseline_ml_only_tennis_h2h_2026", help="Experiment name")
    parser.add_argument("--events", type=int, default=160, help="Number of test events to simulate")
    parser.add_argument("--save-db", action="store_true", default=True, help="Save to experiment_results table")
    args = parser.parse_args()

    cfg = ScientificExperimentRunner.get_fixed_baseline_config(experiment_name=args.name)
    result = ScientificExperimentRunner.run_baseline_ml_experiment(
        config=cfg,
        n_events=args.events,
        save_to_db=args.save_db,
    )
    print_experiment_report(result)
