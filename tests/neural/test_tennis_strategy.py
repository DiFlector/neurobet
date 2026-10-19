"""Policy selection, stake sizing, residual insufficiency, and closing-line value."""

from datetime import datetime, timedelta, timezone

import numpy as np

from app.backtest.config import BacktestConfig
from app.backtest.execution import LATENCY_GRID, SimulatedOddsSnapshot, execute_latency_grid, logit_clv, no_vig_probability
from app.commit import confirming_index, favored_side
from app.compare.candidates.residual import ResidualAblation
from app.compare.rows import CompareRow
from app.metrics import sliced_metrics
from app.strategy import (
    MIN_STAKE_PROBABILITY,
    certainty,
    compare_stake_sizes,
    disagreement,
    prefer_stake_mode,
    promotion_allowed,
    select_policy,
    stake_gate,
)


def _row(event_id: str, label: int) -> CompareRow:
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return CompareRow(
        event_id=event_id,
        observed_at=moment,
        started_at=moment,
        finished_at=moment + timedelta(hours=2),
        phase="prematch",
        label=label,
        odds_a=1.8,
        odds_b=2.0,
        market_prob_a=0.55,
        player_a="A",
        player_b="B",
    )


def test_short_residual_is_insufficient_and_stays_on_the_market():
    rows = [_row(f"m{index}", index % 2) for index in range(10)]
    model = ResidualAblation("elo")
    probs = np.full(len(rows), 0.55)
    model.fit(rows, probs)
    assert model.insufficient is True
    predicted = model.predict(rows, probs)
    assert np.allclose(predicted, 0.55)


def test_policy_requires_positive_validation_clv():
    losing = [
        {"edge": 0.08, "odds": 1.9, "disagreement": 0.02, "confirmations": 2, "clv": -0.04}
        for _ in range(40)
    ]
    assert select_policy(losing)["action"] == "no_bet"
    winning = [
        {"edge": 0.08, "odds": 1.9, "disagreement": 0.02, "confirmations": 2, "clv": 0.03}
        for _ in range(40)
    ]
    chosen = select_policy(winning)
    assert chosen["action"] == "bet"
    assert chosen["min_edge"] == 0.02


def test_stake_modes_keep_the_same_decisions_and_release_stays_blocked():
    decisions = [{"probability": 0.6, "odds": 2.0, "won": index % 3 != 0} for index in range(20)]
    report = compare_stake_sizes(decisions)
    counts = {name: item["bets"] for name, item in report.items()}
    assert len(set(counts.values())) == 1
    assert prefer_stake_mode(report) in report
    assert disagreement([0.62, 0.58, 0.70]) == 0.12
    allowed, reasons = promotion_allowed(0.15, 8, 0.0, -0.02, 0.01)
    assert allowed is False
    assert "shadow_days" in reasons
    assert "paired_observations" in reasons
    assert "counterfactual_roi" in reasons


def test_closing_line_value_rewards_a_better_price():
    close = no_vig_probability(1.80, 2.10, "p1")
    executed = no_vig_probability(2.00, 1.90, "p1")
    assert logit_clv(close, executed) > 0
    tape = [
        SimulatedOddsSnapshot("a", datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc), 2.0, 1.9),
        SimulatedOddsSnapshot("b", datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc), 1.8, 2.1),
    ]
    assert tape[1].p1_odds < tape[0].p1_odds


def test_confirmation_waits_for_the_interval_and_a_third_snapshot():
    start = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    probabilities = [(0.62, 0.38), (0.63, 0.37), (0.64, 0.36)]
    odds = [(1.7, 2.2), (1.7, 2.2), (1.7, 2.2)]
    times = [start, start + timedelta(seconds=10), start + timedelta(seconds=70)]
    assert confirming_index(probabilities, odds, 0.02, times, 60.0, 2) == 2
    assert confirming_index(probabilities, odds, 0.02, times, 0.0, 3) == 2
    assert certainty([0.62, 0.58]) == 0.96


def test_longer_latency_rejects_a_stale_quote():
    moment = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    tape = [SimulatedOddsSnapshot("a", moment, 1.8, 2.1)]
    config = BacktestConfig(execution_latency_sec=0.0, max_odds_staleness_sec=4.0, min_edge=0.0, allow_slippage=False)
    report = execute_latency_grid(config, moment, 0.6, "p1", 1.8, tape)
    assert set(report) == set(LATENCY_GRID)
    assert report[0.0] == "executed"
    assert report[10.0] == "STALE_ODDS"


def test_stake_gate_blocks_journal_and_matches_backtest():
    assert stake_gate(0.619, 1.70) == (False, "LOW_PROBABILITY")
    assert stake_gate(0.62, 1.64) == (False, "ODDS_TOO_SHORT")
    assert stake_gate(0.62, 1.65) == (True, "")
    assert stake_gate(0.62, 2.199) == (True, "")
    assert stake_gate(0.62, 2.20) == (False, "ODDS_TOO_LONG")
    assert stake_gate(0.62, 1.70, "set_winner:2") == (True, "")
    assert stake_gate(0.62, 2.20, "set_winner:2") == (False, "ODDS_TOO_LONG")
    assert stake_gate(0.55, 1.50, "match_winner", "football") == (True, "")
    assert favored_side(0.185, 0.815, 5.60, 1.15, 0.02, sport_code="tennis") is None
    assert favored_side(0.185, 0.815, 5.60, 1.15, 0.02, sport_code="football") == "a"
    assert favored_side(0.62, 0.38, 1.80, 2.20, 0.02, sport_code="tennis") == "a"
    assert favored_side(0.55, 0.45, 2.00, 1.90, 0.02, "set_winner:2", "tennis") is None
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    blocked = confirming_index(
        [(0.18, 0.82), (0.18, 0.82)],
        [(5.60, 1.15), (5.60, 1.15)],
        0.02,
        [start, start + timedelta(seconds=30)],
    )
    assert blocked is None
    assert MIN_STAKE_PROBABILITY == 0.62
    assert BacktestConfig().execution_latency_sec == 2.0


def test_slices_include_feature_version():
    rows = [
        {"market": "match_winner", "odds": 1.5, "phase": "set_1", "feature_version": "2.1.0", "label": 1, "probability": 0.7},
        {"market": "match_winner", "odds": 1.6, "phase": "set_1", "feature_version": "2.1.0", "label": 0, "probability": 0.4},
    ]
    report = sliced_metrics(rows)
    assert "feature:2.1.0" in report
    assert "market:match_winner" in report


if __name__ == "__main__":
    test_short_residual_is_insufficient_and_stays_on_the_market()
    test_policy_requires_positive_validation_clv()
    test_stake_modes_keep_the_same_decisions_and_release_stays_blocked()
    test_closing_line_value_rewards_a_better_price()
    test_confirmation_waits_for_the_interval_and_a_third_snapshot()
    test_longer_latency_rejects_a_stale_quote()
    test_stake_gate_blocks_journal_and_matches_backtest()
    test_slices_include_feature_version()
    print("test_tennis_strategy: OK")
