"""Test-set metrics, reliability bins, and paper bets against the market baseline."""

from collections import defaultdict
from typing import Dict, List, Sequence, Tuple

import numpy as np

from ..backtest.config import BacktestConfig
from ..backtest.execution import ExecutionSimulator, SimulatedOddsSnapshot, logit_clv, no_vig_probability, quote_as_of
from ..commit import confirming_index, favored_side
from ..metrics import compute_classification_metrics
from .rows import CompareRow


def _clip(prob: float) -> float:
    return min(1.0 - 1e-6, max(1e-6, prob))


def reliability_bins(labels: np.ndarray, probs: np.ndarray, n_bins: int = 10) -> List[dict]:
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    points = []
    for index in range(n_bins):
        if index == n_bins - 1:
            mask = (probs >= edges[index]) & (probs <= edges[index + 1])
        else:
            mask = (probs >= edges[index]) & (probs < edges[index + 1])
        count = int(mask.sum())
        points.append(
            {
                "bin": index,
                "count": count,
                "mean_probability": None if count == 0 else round(float(probs[mask].mean()), 4),
                "observed_frequency": None if count == 0 else round(float(labels[mask].mean()), 4),
            }
        )
    return points


def match_log_losses(rows: Sequence[CompareRow], probs: np.ndarray) -> Dict[str, float]:
    grouped: Dict[str, List[float]] = defaultdict(list)
    for row, prob in zip(rows, probs):
        p = _clip(float(prob))
        y = row.label
        grouped[row.event_id].append(-(y * np.log(p) + (1 - y) * np.log(1 - p)))
    return {event_id: float(np.mean(values)) for event_id, values in grouped.items()}


def bootstrap_delta(left: Dict[str, float], right: Dict[str, float], seed: int = 42, draws: int = 400) -> dict:
    """Mean of left-minus-right log loss by match. Negative means left is better."""
    keys = sorted(set(left) & set(right))
    if not keys:
        return {"mean": None, "low": None, "high": None}
    diff = np.asarray([left[key] - right[key] for key in keys], dtype=float)
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(draws):
        pick = rng.integers(0, len(diff), len(diff))
        samples.append(float(diff[pick].mean()))
    samples.sort()
    return {
        "mean": round(float(diff.mean()), 4),
        "low": round(samples[int(0.025 * (len(samples) - 1))], 4),
        "high": round(samples[int(0.975 * (len(samples) - 1))], 4),
    }


def bet_summary(rows: Sequence[CompareRow], probs: np.ndarray) -> dict:
    grouped: Dict[str, List[Tuple[CompareRow, float]]] = defaultdict(list)
    for row, prob in zip(rows, probs):
        grouped[row.event_id].append((row, float(prob)))
    config = BacktestConfig(min_edge=0.02, execution_latency_sec=2.0, max_odds_staleness_sec=15.0)
    simulator = ExecutionSimulator(config)
    profits = []
    clv_values = []
    for items in grouped.values():
        probabilities = [(prob, 1.0 - prob) for _row, prob in items]
        odds = [(row.odds_a, row.odds_b) for row, _prob in items]
        if len(items) >= 2:
            index = confirming_index(probabilities, odds, 0.02, sport_code="tennis")
            if index is None:
                continue
        else:
            index = 0
        row, prob = items[index]
        side = favored_side(prob, 1.0 - prob, row.odds_a, row.odds_b, 0.02, sport_code="tennis")
        if side is None or row.suspended:
            continue
        snapshots = [
            SimulatedOddsSnapshot(str(pos), item.observed_at, item.odds_a, item.odds_b, item.suspended)
            for pos, (item, _prob) in enumerate(items)
        ]
        decision_odds = row.odds_a if side == "a" else row.odds_b
        side_prob = prob if side == "a" else 1.0 - prob
        execution = simulator.execute_order(
            row.observed_at,
            side_prob,
            "p1" if side == "a" else "p2",
            decision_odds,
            snapshots,
        )
        if not execution.is_executed:
            continue
        close = quote_as_of(snapshots, items[-1][0].finished_at or items[-1][0].observed_at)
        executed_quote = quote_as_of(snapshots, execution.execution_timestamp)
        if close is not None and executed_quote is not None:
            clv_values.append(
                logit_clv(
                    no_vig_probability(close.p1_odds, close.p2_odds, "p1" if side == "a" else "p2"),
                    no_vig_probability(executed_quote.p1_odds, executed_quote.p2_odds, "p1" if side == "a" else "p2"),
                )
            )
        won = (side == "a" and row.label == 1) or (side == "b" and row.label == 0)
        profits.append((execution.execution_odds - 1.0) if won else -1.0)
    finite_clv = [value for value in clv_values if value is not None]
    mean_clv = round(float(np.mean(finite_clv)), 4) if finite_clv else None
    if not profits:
        return {"roi": 0.0, "yield": 0.0, "max_drawdown": 0.0, "bets": 0, "win_rate": 0.0, "mean_clv": mean_clv}
    equity = np.cumsum(np.asarray(profits, dtype=float))
    peak = np.maximum.accumulate(equity)
    drawdown = float((peak - equity).max())
    stake = float(len(profits))
    roi = float(sum(profits) / stake)
    wins = sum(1 for value in profits if value > 0)
    return {
        "roi": round(roi, 4),
        "yield": round(roi, 4),
        "max_drawdown": round(drawdown, 4),
        "bets": len(profits),
        "win_rate": round(wins / len(profits), 4),
        "mean_clv": mean_clv,
    }


def score_candidate(rows: Sequence[CompareRow], probs: np.ndarray, market_probs: np.ndarray) -> dict:
    labels = np.asarray([row.label for row in rows], dtype=int)
    metrics = compute_classification_metrics(labels, probs)
    market_metrics = compute_classification_metrics(labels, market_probs)
    market_losses = match_log_losses(rows, market_probs)
    model_losses = match_log_losses(rows, probs)
    delta = bootstrap_delta(model_losses, market_losses)
    n_matches = len({row.event_id for row in rows})
    brier_gap = float(metrics["brier_score"]) - float(market_metrics["brier_score"])
    model_bets = bet_summary(rows, probs)
    market_bets = bet_summary(rows, market_probs)
    model_clv = model_bets.get("mean_clv")
    market_clv = market_bets.get("mean_clv")
    clv_ok = model_clv is None or market_clv is None or float(model_clv) >= float(market_clv)
    beats = bool(
        n_matches >= 100
        and delta.get("high") is not None
        and delta["high"] < 0
        and brier_gap <= 0.02
        and clv_ok
    )
    levels = defaultdict(list)
    for index, row in enumerate(rows):
        levels[row.tour_level].append(index)
    by_level = {}
    for level, indexes in levels.items():
        if len(indexes) < 5:
            continue
        picked = np.asarray(indexes)
        by_level[level] = compute_classification_metrics(labels[picked], np.asarray(probs)[picked])
    return {
        "metrics": metrics,
        "reliability": reliability_bins(labels, np.asarray(probs, dtype=float)),
        "bets": model_bets,
        "market_bets": market_bets,
        "log_loss_minus_market": delta,
        "brier_minus_market": round(brier_gap, 4),
        "test_matches": n_matches,
        "beats_market": beats,
        "by_tour_level": by_level,
    }
