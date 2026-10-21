import importlib.util
import sys
from types import SimpleNamespace

_SPEC = importlib.util.spec_from_file_location(
    "odds_free_tennis_forecast",
    "services/neural/app/tennis_forecast.py",
)
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

TennisState = _MODULE.TennisState
_advance_point = _MODULE._advance_point
_set_game_winner = _MODULE._set_game_winner
_live_serve_rates = _MODULE._live_serve_rates
forecast = _MODULE.forecast
parse_tennis_state = _MODULE.parse_tennis_state
build_odds_free_feature_vector = _MODULE.build_odds_free_feature_vector
validate_odds_free_feature_availability = _MODULE.validate_odds_free_feature_availability
ODDS_FREE_FEATURE_SOURCE_GROUPS = _MODULE.ODDS_FREE_FEATURE_SOURCE_GROUPS
ODDS_FREE_FEATURE_NAMES = _MODULE.ODDS_FREE_FEATURE_NAMES


def _snapshot(points_a: str, points_b: str):
    return SimpleNamespace(
        score_detail={
            "sets": [],
            "current_game": {"points_a": points_a, "points_b": points_b, "is_tiebreak": False},
        },
        sport_state={
            "server": "player_a",
            "stats": {
                "sets_a": 0,
                "sets_b": 0,
                "set_lines": [{"games_a": 0, "games_b": 0, "live": True}],
            },
        },
    )


def test_odds_free_candidate_feature_contract_is_closed_and_ignores_prices():
    state = TennisState(games_a=2, games_b=1, points_a=3, points_b=2, server="a")
    pair = {
        "elo_a": 1600, "elo_b": 1550, "elo_diff": 50,
        "win_rate_90d_a": 0.7, "win_rate_90d_b": 0.4, "win_rate_90d_diff": 0.3,
        "match_count_a": 12, "match_count_b": 9,
        "player_metrics_missing_a": False, "player_metrics_missing_b": False,
        "odds_a": 1.1, "odds_b": 8.0, "market_probability_a": 0.9,
    }
    serve_a, serve_b = (0.68, 0.62, 0.7), (0.61, 0.62, 0.5)
    first = build_odds_free_feature_vector(state, pair, serve_a, serve_b, "clay")
    changed_prices = {**pair, "odds_a": 30.0, "odds_b": 1.01, "market_probability_a": 0.02}
    second = build_odds_free_feature_vector(state, changed_prices, serve_a, serve_b, "clay")
    assert len(first) == len(ODDS_FREE_FEATURE_NAMES) == 30
    assert first == second
    assert not any(token in name for name in ODDS_FREE_FEATURE_NAMES for token in ("odds", "market", "price", "line"))


def test_feature_availability_certificate_fails_closed_for_missing_or_future_sources():
    from datetime import datetime, timezone
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    complete = {source: as_of.isoformat() for source in ODDS_FREE_FEATURE_SOURCE_GROUPS}
    assert validate_odds_free_feature_availability(as_of, complete)["certified"] is True
    complete.pop("serve_metrics_b")
    complete["event_surface"] = "2026-01-02T00:00:00+00:00"
    result = validate_odds_free_feature_availability(as_of, complete)
    assert result["certified"] is False
    assert result["missing_sources"] == ["serve_metrics_b"]
    assert result["future_sources"] == ["event_surface"]


def test_adapter_score_shape_uses_live_line_instead_of_last_closed_set():
    detail = {
        "sets": [{"games_a": 6, "games_b": 4}],
        "current_game": {"points_a": "15", "points_b": "0", "is_tiebreak": False},
    }
    sport = {
        "server": "player_b",
        "stats": {
            "sets_a": 1,
            "sets_b": 0,
            "set_lines": [
                {"games_a": 6, "games_b": 4, "live": False},
                {"games_a": 2, "games_b": 1, "live": True},
            ],
        },
    }
    state, error = parse_tennis_state(detail, sport)
    assert error is None
    assert state.sets_a == 1 and state.games_a == 2 and state.games_b == 1
    assert state.points_a == 1 and state.server == "b"


def test_deuce_and_advantage_transitions_are_legal_and_bounded():
    deuce = TennisState(points_a=3, points_b=3, server="a")
    advantage_a = _advance_point(deuce, "a")
    assert (advantage_a.points_a, advantage_a.points_b) == (4, 3)
    assert _advance_point(advantage_a, "b").points_a == 3
    assert _advance_point(advantage_a, "b").points_b == 3
    assert _advance_point(advantage_a, "a").games_a == 1


def test_server_rotates_after_set_and_tiebreak_set_closes():
    state = TennisState(games_a=5, games_b=4, server="b")
    closed = _set_game_winner(state, "a")
    assert closed.sets_a == 1 and closed.server == "a"

    tiebreak = TennisState(games_a=6, games_b=6, points_a=6, points_b=5, server="b", tiebreak=True, tiebreak_server="a")
    closed = _advance_point(tiebreak, "a")
    assert closed.sets_a == 1 and closed.games_a == closed.games_b == 0
    assert closed.server == "b"


def test_repeated_polls_and_score_gaps_do_not_become_live_point_evidence():
    rows = [_snapshot("0", "0"), _snapshot("15", "0"), _snapshot("15", "0"), _snapshot("15", "15")]
    rate_a, rate_b, samples = _live_serve_rates(rows, (0.62, 0.62, 0.0), (0.62, 0.62, 0.0))
    assert samples == {"a": 0.0, "b": 0.0}
    assert rate_a[0] == 0.62
    assert rate_b[0] == 0.62


def test_numeric_tiebreak_score_is_parsed_and_invalid_format_is_unavailable():
    detail = {
        "current_game": {"points_a": "8", "points_b": "7", "is_tiebreak": True},
    }
    sport = {
        "server": "player_a",
        "tiebreak_server": "player_a",
        "tiebreak": True,
        "stats": {"set_lines": [{"games_a": 6, "games_b": 6, "live": True}]},
    }
    state, error = parse_tennis_state(detail, sport)
    assert error is None and state is not None
    assert (state.points_a, state.points_b, state.tiebreak) == (8, 7, True)

    malformed = {"stats": {"set_lines": [{"games_a": 7, "games_b": 7, "live": True}]}}
    state, error = parse_tennis_state({}, malformed)
    assert state is None and error == "UNSUPPORTED_SCORE"


def test_forecast_scenarios_are_normalized_through_deuce_and_tiebreak():
    states = [
        TennisState(points_a=3, points_b=3, server="a"),
        TennisState(games_a=6, games_b=6, points_a=6, points_b=6, server="a", tiebreak=True, tiebreak_server="a"),
    ]
    for state in states:
        result = forecast(state, serve_a=(0.64, 0.62, 0.5), serve_b=(0.61, 0.62, 0.4), data_quality=0.8)
        assert 0 <= result["probabilities"]["match_a"] <= 1
        assert abs(sum(row["probability"] for row in result["scenarios"]["next_3_points"]) - 1) < 1e-5
        assert abs(sum(row["probability"] for row in result["scenarios"]["next_2_games"]) - 1) < 1e-5


def test_unknown_server_is_bracketed_once_and_kept_inside_each_scenario():
    state = TennisState(server=None)
    result = forecast(state, serve_a=(0.72, 0.62, 0.5), serve_b=(0.55, 0.62, 0.5))
    scenarios = result["scenarios"]["next_3_points"]
    assert abs(sum(item["probability"] for item in scenarios) - 1) < 1e-5
    assert {item["state"]["server"] for item in scenarios} == {"a", "b"}
