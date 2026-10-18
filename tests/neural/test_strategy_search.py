"""Gate-search windows stay disjoint and the ranking does not touch the ledger."""

from datetime import datetime, timedelta, timezone

from app.replay.strategy_search import (
    choose_gates,
    iter_gate_configs,
    iter_range_gate_configs,
    prefer_gate,
    price_windows,
    simulate_gates,
)
from app.set_match import checkpoint_scores_sets
from app.replay.strategy_split import split_match_ids


def test_booster_split_keeps_validation_after_the_fit():
    from types import SimpleNamespace

    from app.replay.booster_compare import split_train

    matches = [SimpleNamespace(event_id=str(index), started_at=index) for index in range(100)]
    base, blend, validation = split_train(matches)
    assert base and blend and validation
    assert max(item.started_at for item in base) < min(item.started_at for item in blend)
    assert max(item.started_at for item in blend) < min(item.started_at for item in validation)
    assert len(base) + len(blend) + len(validation) == 100


def test_windows_are_disjoint_and_blocked_matches_stay_out_of_search():
    newest = [f"{index:08d}-0000-4000-8000-000000000000" for index in range(500)]
    blocked = set(newest[120:150])
    windows = split_match_ids(newest, blocked)
    groups = [windows["gate_final"], windows["model_final"], windows["search"], windows["train"], windows["blocked"]]
    flat = [item for group in groups for item in group]
    assert len(flat) == len(set(flat))
    assert windows["gate_final"] == newest[:100]
    assert not blocked.intersection(windows["search"])
    assert not blocked.intersection(windows["train"])
    assert not blocked.intersection(windows["model_final"])
    assert set(windows["blocked"]) == blocked


def test_price_windows_do_not_overlap():
    assert price_windows()
    for window in price_windows():
        assert window["short_odds_lo"] < window["short_odds_hi"] <= window["mid_odds_lo"]
        assert window["mid_odds_lo"] < window["mid_odds_hi"] < window["match_odds_hi"]
    assert any(item["mid_odds_lo"] == 1.40 for item in price_windows())


def test_choose_gates_is_stable_and_rejects_a_weak_sample():
    weak = {"params": {"min_edge": 0.02}, "bets": 5, "roi": 0.9, "max_drawdown": 10, "bankruptcies": 0}
    better = {"params": {"min_edge": 0.04}, "bets": 40, "roi": 0.05, "max_drawdown": 400, "bankruptcies": 0}
    riskier = {"params": {"min_edge": 0.03}, "bets": 80, "roi": 0.05, "max_drawdown": 900, "bankruptcies": 0}
    broke = {"params": {"min_edge": 0.06}, "bets": 40, "roi": 0.2, "max_drawdown": 100, "bankruptcies": 1}
    assert choose_gates([weak, riskier, better, broke])["params"] == better["params"]
    assert choose_gates([weak, broke]) is None


def test_recorded_tape_places_two_confirmations_and_keeps_the_ledger_closed():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(2):
        rows.append(
            {
                "event_id": "11111111-1111-4111-8111-111111111111",
                "market": "match_winner",
                "snapshot_at": (start + timedelta(minutes=index)).isoformat(),
                "side": "player_a",
                "probability": 0.62,
                "odds": 1.70,
                "edge": 0.054,
                "winner": "player_a",
                "fill_code": "",
                "fill_odds": 1.70,
                "staleness_sec": 2.0,
            }
        )
    params = next(iter_gate_configs())
    params.update({"min_edge": 0.02, "min_probability": 0.40, "confirmations": 2, "max_odds_staleness_sec": 15})
    report = simulate_gates(rows, params)
    assert report["bets"] == 1
    assert report["wins"] == 1
    assert report["roi"] > 0
    assert "ledger_entries" not in report


def test_edge_below_the_gate_breaks_confirmation():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index, edge in enumerate((0.05, 0.01, 0.05)):
        rows.append(
            {
                "event_id": "22222222-2222-4222-8222-222222222222",
                "market": "match_winner",
                "snapshot_at": (start + timedelta(minutes=index)).isoformat(),
                "side": "player_a",
                "probability": 0.62,
                "odds": 1.70,
                "edge": edge,
                "winner": "player_a",
                "fill_code": "",
                "fill_odds": 1.70,
                "staleness_sec": 2.0,
            }
        )
    params = {
        "short_odds_lo": 1.30,
        "short_odds_hi": 1.40,
        "mid_odds_lo": 1.60,
        "mid_odds_hi": 1.80,
        "match_odds_hi": 2.40,
        "min_edge": 0.03,
        "min_probability": 0.40,
        "confirmations": 2,
        "max_odds_staleness_sec": 15,
        "observe_set": 1,
    }
    assert simulate_gates(rows, params)["bets"] == 0


def test_single_range_wins_unless_three_windows_are_clearly_better():
    ranged = {"roi": 0.05, "max_drawdown": 400}
    close = {"roi": 0.06, "max_drawdown": 400}
    clear = {"roi": 0.08, "max_drawdown": 300}
    assert prefer_gate(ranged, close) is ranged
    assert prefer_gate(ranged, clear) is clear


def test_range_gate_rejects_a_price_outside_the_window():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(2):
        rows.append(
            {
                "event_id": "33333333-3333-4333-8333-333333333333",
                "market": "match_winner",
                "snapshot_at": (start + timedelta(minutes=index)).isoformat(),
                "side": "player_a",
                "probability": 0.62,
                "odds": 2.40,
                "edge": 0.08,
                "winner": "player_a",
                "fill_code": "",
                "fill_odds": 2.40,
                "staleness_sec": 2.0,
            }
        )
    params = next(iter_range_gate_configs())
    params.update({"min_odds": 1.40, "max_odds": 2.10, "min_edge": 0.02, "min_probability": 0.40, "confirmations": 2, "max_odds_staleness_sec": 15})
    assert simulate_gates(rows, params)["bets"] == 0


def test_assigned_lines_split_match_and_set_scoring():
    features = ["market_is_set", "set_index"]
    assert checkpoint_scores_sets({"model_name": "tennis_hist_gradient_roi", "version_tag": "tennis_v3.1.0", "feature_names": features}) is False
    assert checkpoint_scores_sets({"model_name": "tennis_hist_gradient_roi", "version_tag": "tennis_v3.3.1", "feature_names": features}) is False
    assert checkpoint_scores_sets({"model_name": "tennis_gradient_boosting_epoch", "version_tag": "tennis_v3.0.0", "feature_names": features}) is True


def test_gate_grid_contains_the_live_window_and_a_lower_middle_start():
    found_live = False
    found_lower_middle = False
    for params in iter_gate_configs():
        if params["mid_odds_lo"] == 1.40 and params["short_odds_lo"] == 1.30:
            found_lower_middle = True
        if (
            params["short_odds_lo"] == 1.30
            and params["mid_odds_lo"] == 1.60
            and params["match_odds_hi"] == 2.40
            and params["min_edge"] == 0.02
            and params["confirmations"] == 2
        ):
            found_live = True
    assert found_live and found_lower_middle


if __name__ == "__main__":
    test_booster_split_keeps_validation_after_the_fit()
    test_windows_are_disjoint_and_blocked_matches_stay_out_of_search()
    test_price_windows_do_not_overlap()
    test_choose_gates_is_stable_and_rejects_a_weak_sample()
    test_recorded_tape_places_two_confirmations_and_keeps_the_ledger_closed()
    test_edge_below_the_gate_breaks_confirmation()
    test_single_range_wins_unless_three_windows_are_clearly_better()
    test_range_gate_rejects_a_price_outside_the_window()
    test_assigned_lines_split_match_and_set_scoring()
    test_gate_grid_contains_the_live_window_and_a_lower_middle_start()
    print("test_strategy_search: OK")
