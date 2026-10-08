import argparse
import json
import logging
import sys
from datetime import datetime

from .config import BacktestConfig
from .engine import WalkForwardBacktester, generate_synthetic_backtest_samples

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

    config = BacktestConfig(
        sport_code=args.sport,
        initial_bankroll=args.bankroll,
        stake_strategy=args.strategy,
        kelly_fraction=args.kelly_fraction,
        n_splits=args.splits,
        execution_latency_sec=args.latency,
    )

    backtester = WalkForwardBacktester(config=config)
    samples = generate_synthetic_backtest_samples(n_events=args.events, random_seed=config.random_seed)

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


if __name__ == "__main__":
    main()
