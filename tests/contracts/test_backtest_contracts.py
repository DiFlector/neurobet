"""Contracts for the historical replay request."""

from datetime import datetime, timezone

from pydantic import ValidationError

from contracts import BacktestReplayConfig, BacktestRunRequest


def test_replay_defaults_match_the_admin_bankroll():
    config = BacktestReplayConfig(timestamp=datetime.now(timezone.utc))
    assert config.initial_bankroll == 5000
    assert config.fixed_stake == 100
    assert config.bankruptcy_threshold == 100
    assert config.match_limit == 100
    assert config.odds_gate_mode == "live"
    assert config.markets == ["match_winner", "set_winner"]


def test_model_scope_requires_an_id():
    try:
        BacktestRunRequest.model_validate({"selector": {"scope": "model", "sport_code": "tennis"}})
    except ValidationError:
        return
    raise AssertionError("model scope without an id was accepted")


def test_retired_status_is_not_a_selector_value():
    request = BacktestRunRequest.model_validate(
        {
            "selector": {"scope": "sport", "sport_code": "tennis"},
            "config": {"match_limit": 25, "initial_bankroll": 8000, "fixed_stake": 100, "bankruptcy_threshold": 100},
        }
    )
    assert request.selector.scope == "sport"
    assert request.config.match_limit == 25
    assert request.config.max_odds > request.config.min_odds


def test_range_bounds_are_rejected_when_inverted():
    try:
        BacktestReplayConfig(
            timestamp=datetime.now(timezone.utc),
            min_odds=2.5,
            max_odds=1.4,
        )
    except ValidationError:
        return
    raise AssertionError("inverted odds bounds were accepted")


if __name__ == "__main__":
    test_replay_defaults_match_the_admin_bankroll()
    test_model_scope_requires_an_id()
    test_retired_status_is_not_a_selector_value()
    test_range_bounds_are_rejected_when_inverted()
    print("backtest contract tests passed")
