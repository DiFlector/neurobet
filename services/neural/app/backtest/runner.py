import argparse
import json
import logging
import sys
from datetime import datetime

import numpy as np

from .config import BacktestConfig
from .engine import BacktestSample, WalkForwardBacktester

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("neural.backtest.cli")


def print_ascii_report(summary, config: BacktestConfig, model_type: str) -> None:
    """Print beautifully formatted backtest report to terminal."""
    print("\n" + "=" * 70)
    print(" 🚀 NEUROBET WALK-FORWARD BACKTEST REPORT ")
    print("=" * 70)
    print(f" Sport:              {config.sport_code.upper()} ({config.market})")
    print(f" Model Type:         {model_type.upper()}")
    print(f" Staking Strategy:   {config.stake_strategy} (Kelly Mult: {config.kelly_fraction if config.stake_strategy == 'KELLY' else 'N/A'})")
    print(f" Walk-Forward Folds: {config.n_splits} ({config.split_mode})")
    print(f" Execution Latency:  {config.execution_latency_sec}s | Staleness Cap: {config.max_odds_staleness_sec}s")
    print("-" * 70)
    print(f" Initial Bankroll:   {summary.initial_bankroll:,.2f} RUB")
    print(f" Final Bankroll:     {summary.final_bankroll:,.2f} RUB")
    pnl_sign = "+" if summary.net_pnl >= 0 else ""
    print(f" Net P&L:            {pnl_sign}{summary.net_pnl:,.2f} RUB")
    print(f" ROI:                {summary.roi * 100:+.2f}%")
    print(f" Turnover:           {summary.turnover:,.2f} RUB")
    print(f" Win Rate:           {summary.win_rate * 100:.2f}%")
    print(f" Total Bets Placed:  {summary.total_bets} (Won: {summary.bets_won}, Lost: {summary.bets_lost}, Void: {summary.bets_void})")
    print(f" Peak Equity:        {summary.peak_bankroll:,.2f} RUB")
    print(f" Min Equity:         {summary.min_bankroll:,.2f} RUB")
    print(f" Peak Exposure:      {summary.peak_exposure:,.2f} RUB")
    print(f" Max Drawdown:       {summary.max_drawdown_pct * 100:.2f}% ({summary.max_drawdown_amount:,.2f} RUB)")
    print("-" * 70)
    print(" 📊 ODDS BUCKET ANALYSIS:")
    print(f" {'Bucket':<14} | {'Bets':<5} | {'WinRate':<8} | {'Turnover':<10} | {'P&L (RUB)':<11} | {'ROI':<7}")
    print("-" * 70)
    for name, b in summary.odds_buckets.items():
        if b.total_bets > 0:
            print(f" {name:<14} | {b.total_bets:<5} | {b.win_rate*100:>6.1f}% | {b.turnover:>10.2f} | {b.net_pnl:>+11.2f} | {b.roi*100:>+6.1f}%")

    print("-" * 70)
    print(" 📊 EDGE BUCKET ANALYSIS:")
    print(f" {'Bucket':<14} | {'Bets':<5} | {'WinRate':<8} | {'Turnover':<10} | {'P&L (RUB)':<11} | {'ROI':<7}")
    print("-" * 70)
    for name, b in summary.edge_buckets.items():
        if b.total_bets > 0:
            print(f" {name:<14} | {b.total_bets:<5} | {b.win_rate*100:>6.1f}% | {b.turnover:>10.2f} | {b.net_pnl:>+11.2f} | {b.roi*100:>+6.1f}%")
    print("=" * 70 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Neurobet Walk-Forward Backtester CLI")
    parser.add_argument("--sport", default="tennis", help="Sport code (default: tennis)")
    parser.add_argument("--model", choices=["gradient_boosting", "logistic_regression"], default="gradient_boosting")
    parser.add_argument("--strategy", choices=["FIXED", "PERCENT", "KELLY"], default="KELLY")
    parser.add_argument("--kelly-fraction", type=float, default=0.25, help="Kelly multiplier (default: 0.25)")
    parser.add_argument("--bankroll", type=float, default=100000.0, help="Initial bankroll (default: 100000.0)")
    parser.add_argument("--splits", type=int, default=4, help="Number of walk-forward splits (default: 4)")
    parser.add_argument("--latency", type=float, default=2.0, help="Execution latency in seconds (default: 2.0)")
    parser.add_argument("--events", type=int, default=150, help="Number of test events to simulate (default: 150)")
    parser.add_argument("--output-json", default=None, help="Optional path to save JSON report")

    args = parser.parse_args()

    samples, n_events = _load_real_samples(args.sport.lower())
    splits = _feasible_splits(n_events, args.splits)
    if splits != args.splits:
        logger.info("Walk-forward folds reduced from %d to %d because only %d matches are available.", args.splits, splits, n_events)

    config = BacktestConfig(
        sport_code=args.sport,
        initial_bankroll=args.bankroll,
        stake_strategy=args.strategy,
        kelly_fraction=args.kelly_fraction,
        n_splits=splits,
        execution_latency_sec=args.latency,
        min_edge=0.02,
        confirm_side=True,
        group_by_event=True,
    )

    backtester = WalkForwardBacktester(config=config)

    summary = backtester.run(samples=samples, model_type=args.model, save_to_db=True)
    print_ascii_report(summary, config, args.model)

    if args.output_json:
        report_data = {
            "config": config.model_dump(),
            "summary": {
                "initial_bankroll": summary.initial_bankroll,
                "final_bankroll": summary.final_bankroll,
                "net_pnl": summary.net_pnl,
                "roi": summary.roi,
                "turnover": summary.turnover,
                "win_rate": summary.win_rate,
                "total_bets": summary.total_bets,
                "bets_won": summary.bets_won,
                "bets_lost": summary.bets_lost,
                "max_drawdown_amount": summary.max_drawdown_amount,
                "max_drawdown_pct": summary.max_drawdown_pct,
            },
        }
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        print(f"Report saved to {args.output_json}")


def _feasible_splits(n_matches: int, requested: int) -> int:
    splits = requested
    while splits >= 2 and n_matches < (splits + 1) * 2:
        splits -= 1
    if splits < 2:
        raise SystemExit(
            f"Нужно минимум 6 завершённых матчей с линией для walk-forward, сейчас {n_matches}."
        )
    return splits


def _load_real_samples(sport_code: str):
    from db.connection import SessionLocal

    from app.commit import side_odds_from_row
    from app.dataset import get_dataset_builder

    from .execution import SimulatedOddsSnapshot

    try:
        with SessionLocal() as session:
            matches, feature_names = get_dataset_builder(sport_code).load_matches(session)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    samples = []
    for match in matches:
        priced = []
        for index, (row, moment) in enumerate(zip(match.rows, match.observed_at)):
            market = "match_winner" if not match.row_markets else str(match.row_markets[index])
            if market != "match_winner":
                continue
            odds_a, odds_b = side_odds_from_row(row, feature_names)
            if odds_a <= 1.0 or odds_b <= 1.0:
                continue
            priced.append((row, moment, odds_a, odds_b))
        tape = [
            SimulatedOddsSnapshot(
                snapshot_id=f"{match.event_id}:{moment.isoformat()}",
                observed_at=moment,
                p1_odds=odds_a,
                p2_odds=odds_b,
            )
            for _row, moment, odds_a, odds_b in priced
        ]
        finished_at = priced[-1][1] if priced else None
        for row, moment, odds_a, odds_b in priced:
            samples.append(
                BacktestSample(
                    event_id=match.event_id,
                    decision_timestamp=moment,
                    feature_vector=np.asarray(row, dtype=np.float32),
                    p1_odds=odds_a,
                    p2_odds=odds_b,
                    odds_snapshots=tape,
                    actual_winner="p1" if match.label == 1 else "p2",
                    finished_at=finished_at,
                    market="match_winner",
                )
            )
    samples.sort(key=lambda sample: (sample.decision_timestamp, sample.event_id))
    logger.info("Loaded %d priced snapshots from %d real %s matches.", len(samples), len(matches), sport_code)
    return samples, len(matches)


if __name__ == "__main__":
    main()
