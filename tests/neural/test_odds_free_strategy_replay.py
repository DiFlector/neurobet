from datetime import datetime, timedelta, timezone

from tennis_adapter.odds_free_replay import replay_odds_free_forecasts
from tennis_adapter.policy import TennisPolicy


def _policy(confirmations=3):
    return TennisPolicy(
        match_gate_mode="range", set_gate_mode="range",
        match_min_odds=1.65, match_max_odds=2.2,
        set_min_odds=1.55, set_max_odds=2.2,
        match_min_probability=0.62, set_min_probability=0.62,
        match_min_edge=0.04, match_max_edge=0.07,
        set_min_edge=0.04, set_max_edge=0.07,
        match_confirmations=confirmations, set_confirmations=confirmations,
        observe_set=1, stake_sets="2", min_data_quality=50,
        max_context_age_seconds=180,
    )


def _fixtures(count=3):
    start = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    forecasts, odds = [], []
    for index in range(count):
        at = start + timedelta(seconds=index * 30)
        forecasts.append({
            "forecast_id": f"f{index}", "event_id": "event-1",
            "model_name": "tennis_score_baseline", "model_version": "tennis_v1.0.0",
            "observed_at": at.isoformat(), "calculated_at": (at + timedelta(seconds=1)).isoformat(),
            "forecast_available": True, "probabilities": {"match_a": 0.65, "current_set_a": 0.65},
            "state": {"sets_a": 1, "sets_b": 0}, "data_quality": 0.8,
        })
        quote_at = at + timedelta(seconds=0.5)
        for market, line in (("match_winner", None), ("set_winner", 2)):
            odds.extend([
                {"event_id": "event-1", "market_type": market, "line_value": line, "outcome": "player_a", "odds": 1.7,
                 "status": "active", "observed_at": quote_at.isoformat()},
                {"event_id": "event-1", "market_type": market, "line_value": line, "outcome": "player_b", "odds": 2.0,
                 "status": "active", "observed_at": quote_at.isoformat()},
            ])
    return start, forecasts, odds


def test_read_only_replay_applies_tennis_cfg_markets_and_confirmations():
    _, forecasts, odds = _fixtures()
    result = replay_odds_free_forecasts(forecasts, odds, policy=_policy())
    assert result["mode"] == "read_only_candidate_replay"
    assert result["writes_bets"] is False
    assert result["writes_bankroll"] is False
    assert [(row["market"], row["outcome"]) for row in result["signals"]] == [
        ("match_winner", "player_a"), ("set_winner:2", "player_a")
    ]
    assert all(row["confirmations"] == 3 for row in result["signals"])
    assert all(row["expected_value"] > 0 for row in result["signals"])


def test_positive_ev_mode_rejects_candidate_when_configured_edge_is_non_positive():
    _, forecasts, odds = _fixtures()
    policy = _policy(confirmations=1)
    policy = TennisPolicy(**{
        **policy.as_dict(), "match_min_edge": -0.1, "match_max_edge": None,
        "set_min_edge": -0.1, "set_max_edge": None,
        "match_min_probability": 0.5, "set_min_probability": 0.5,
    })
    for row in odds:
        if row["outcome"] == "player_a":
            row["odds"] = 1.65
    for row in forecasts:
        row["probabilities"] = {"match_a": 0.60, "current_set_a": 0.60}
    result = replay_odds_free_forecasts(
        forecasts[:1], odds[:4], policy=policy, confirm_positive_ev=True,
    )
    assert result["signals"] == []
    assert result["counts"].get("NON_POSITIVE_EV", 0) > 0


def test_future_and_stale_quotes_are_never_used_for_a_past_forecast():
    start, forecasts, _ = _fixtures(count=1)
    forecast_at = datetime.fromisoformat(forecasts[0]["calculated_at"])
    future = forecast_at + timedelta(seconds=1)
    stale = start - timedelta(hours=1)
    odds = [
        {"event_id": "event-1", "market_type": "match_winner", "outcome": "player_a", "odds": 1.7,
         "status": "active", "observed_at": future.isoformat()},
        {"event_id": "event-1", "market_type": "match_winner", "outcome": "player_b", "odds": 2.0,
         "status": "active", "observed_at": stale.isoformat()},
    ]
    result = replay_odds_free_forecasts(forecasts, odds, policy=_policy(confirmations=1))
    assert result["signals"] == []
    assert result["counts"].get("TWO_SIDED_PRICE_MISSING", 0) == 2


def test_duplicate_timestamp_does_not_satisfy_multiple_confirmations():
    _, forecasts, odds = _fixtures(count=1)
    forecasts = [forecasts[0], {**forecasts[0], "forecast_id": "duplicate"}]
    result = replay_odds_free_forecasts(forecasts, odds, policy=_policy(confirmations=2))
    assert result["signals"] == []
    assert result["counts"].get("DUPLICATE_OR_OUT_OF_ORDER_FORECAST") == 2


def test_unavailable_forecast_breaks_a_confirmation_streak():
    _, forecasts, odds = _fixtures(count=2)
    unavailable = {**forecasts[1], "forecast_available": False}
    forecasts[1] = unavailable
    later = {**forecasts[0], "forecast_id": "later", "observed_at": "2026-10-21T12:02:01+00:00",
             "calculated_at": "2026-10-21T12:02:02+00:00"}
    forecasts.append(later)
    result = replay_odds_free_forecasts(forecasts, odds, policy=_policy(confirmations=2))
    assert result["signals"] == []
    assert result["counts"].get("FORECAST_UNAVAILABLE") == 1


def test_prices_from_another_event_cannot_be_joined_to_forecast():
    _, forecasts, odds = _fixtures(count=3)
    odds = [{**row, "event_id": "another-event"} for row in odds]
    result = replay_odds_free_forecasts(forecasts, odds, policy=_policy())
    assert result["signals"] == []
    assert result["counts"].get("TWO_SIDED_PRICE_MISSING") == 6
