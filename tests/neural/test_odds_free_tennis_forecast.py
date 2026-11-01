import importlib.util
import sys
import pytest
from datetime import datetime, timezone
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
point_in_time_surface = _MODULE.point_in_time_surface
point_in_time_best_of = _MODULE.point_in_time_best_of
infer_live_best_of_from_continuation = _MODULE.infer_live_best_of_from_continuation
ODDS_FREE_FEATURE_SOURCE_GROUPS = _MODULE.ODDS_FREE_FEATURE_SOURCE_GROUPS
ODDS_FREE_FEATURE_NAMES = _MODULE.ODDS_FREE_FEATURE_NAMES
ODDS_FREE_FEATURE_VERSION = _MODULE.ODDS_FREE_FEATURE_VERSION
build_odds_free_baseline_feature_vector = _MODULE.build_odds_free_baseline_feature_vector
independent_strength_prior = _MODULE.independent_strength_prior
apply_independent_strength_prior = _MODULE.apply_independent_strength_prior
serve_retrospective_flags = _MODULE.serve_retrospective_flags
player_serve_rate_snapshot = _MODULE.player_serve_rate_snapshot
from contracts.tennis_pit import resolve_event_attribute_observations
from contracts.tennis_pit import content_addressed_object_matches_hash


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
    assert len(first) == len(ODDS_FREE_FEATURE_NAMES) == 26
    assert "sets_to_win" not in ODDS_FREE_FEATURE_SOURCE_GROUPS["event_state"]
    assert ODDS_FREE_FEATURE_SOURCE_GROUPS["event_format"] == ("sets_to_win", "match_format_known")
    assert "event_surface" not in ODDS_FREE_FEATURE_SOURCE_GROUPS
    assert ODDS_FREE_FEATURE_VERSION == "tennis_odds_free_v2"
    assert first == second
    assert first[-2:] == (0.0, 0.0)
    assert first == build_odds_free_feature_vector(state, pair, serve_a, serve_b, "grass")
    known_pair = {**pair, "match_format_known": True}
    assert build_odds_free_feature_vector(state, known_pair, serve_a, serve_b)[-2:] == (2.0, 1.0)
    assert "odds" not in _MODULE.forecast.__code__.co_varnames
    assert not any(token in name for name in ODDS_FREE_FEATURE_NAMES for token in ("odds", "market", "price", "line"))
    forecast_before_price_change = forecast(state, serve_a=serve_a, serve_b=serve_b)
    forecast_after_price_change = forecast(state, serve_a=serve_a, serve_b=serve_b)
    assert forecast_before_price_change == forecast_after_price_change


def test_prelabel_pit_readiness_requires_timely_hash_backed_evidence(monkeypatch):
    import sys
    from datetime import timedelta
    from types import ModuleType

    class Column:
        def __eq__(self, _): return self
        def __le__(self, _): return self
        def __lt__(self, _): return self
        def asc(self): return self

    class ObservationModel:
        event_id = Column()
        available_at = Column()
        id = Column()

    fake_model = ModuleType("db.models.external_tennis")
    fake_model.TennisEventAttributeObservation = ObservationModel
    monkeypatch.setitem(sys.modules, "db.models.external_tennis", fake_model)

    class Query:
        def __init__(self, rows): self.rows = rows
        def filter(self, *_): return self
        def order_by(self, *_): return self
        def all(self): return self.rows

    class Session:
        def __init__(self, rows): self.rows = rows
        def query(self, _): return Query(self.rows)

    now = datetime(2026, 10, 27, 7, 0, tzinfo=timezone.utc)
    digest = "a" * 64
    rows = [
        SimpleNamespace(id="format", attribute_name="best_of", value_json={"value": 3},
                        source="live_tennis_api", available_at=now, identity_available_at=now,
                        raw_object_key=f"events/{digest}.json", source_content_hash=digest,
                        parser_version="test-v1"),
        SimpleNamespace(id="surface", attribute_name="surface", value_json={"value": "hard"},
                        source="live_tennis_api", available_at=now, identity_available_at=now,
                        raw_object_key=f"events/{digest}.json", source_content_hash=digest,
                        parser_version="test-v1"),
    ]
    def player(side, when=now, has_serve=True):
        return {
            "source_player_id": f"p-{side}", "metric_snapshot_id": f"metric-{side}",
            "available_at": when.isoformat(), "identity_available_at": now.isoformat(),
            "history_digest": f"{side}" * 64,
            "canonical_metric_snapshot_id": f"canonical-{side}" if has_serve else None,
            "canonical_metrics_available_at": now.isoformat(),
            "canonical_metrics_digest": f"{side}" * 64,
            "canonical_metrics": {"serve": {"points": 250}} if has_serve else {},
        }
    state = SimpleNamespace(id="state-1", observed_at=now, raw_hash=digest,
                            raw_object_key=f"states/{digest}.json")
    event = SimpleNamespace(id="event-1")
    ready = _MODULE.prelabel_pit_readiness(
        Session(rows), event, state, now, player("a"), player("b"), state_valid=True
    )
    assert ready["certified"] is True
    assert ready["status"] == "ready_for_labeled_pit"
    assert ready["label_status"] == "pending"

    state.raw_hash = None
    state.raw_object_key = None
    db_only = _MODULE.prelabel_pit_readiness(
        Session(rows), event, state, now, player("a"), player("b"), state_valid=True
    )
    assert db_only["certified"] is True
    assert db_only["validation"]["evidence_policy"] == "tennis_pit_db_state_v2"
    invalid_state = _MODULE.prelabel_pit_readiness(
        Session(rows), event, state, now, player("a"), player("b"), state_valid=False
    )
    assert invalid_state["certified"] is False
    assert "event_state_not_parseable" in invalid_state["reasons"]

    late_identity = _MODULE.prelabel_pit_readiness(
        Session(rows), event, state, now, player("a"), player("b", now + timedelta(seconds=1)),
        state_valid=True,
    )
    assert late_identity["certified"] is True
    assert "b" in late_identity["player_history_fallback"]
    assert "player_metrics" not in late_identity["validation"]["future_sources"]

    missing_serve = _MODULE.prelabel_pit_readiness(
        Session(rows), event, state, now, player("a"), player("b", has_serve=False), state_valid=True
    )
    assert missing_serve["certified"] is True
    assert missing_serve["serve_history_missing"]["b"] is True
    assert "b" in missing_serve["serve_history_fallback"]
    assert content_addressed_object_matches_hash(digest, f"states/{digest}.json")


def test_independent_strength_prior_uses_equal_available_components_and_is_symmetric():
    a = {
        "independent_metrics_available_a": True, "independent_metrics_available_b": True,
        "independent_elo_a": 1700, "independent_elo_b": 1300,
        "independent_glicko_a": 1700, "independent_glicko_b": 1300,
        "independent_rd_a": 50, "independent_rd_b": 50,
        "tour_rank_a": 1, "tour_rank_b": 100, "ranking_comparable": True,
        "rolling_90d_summary_a": {"wins": 9, "matches": 10},
        "rolling_90d_summary_b": {"wins": 1, "matches": 10},
        "h2h_available": True, "h2h_matches": 4, "h2h_wins_a": 3, "h2h_wins_b": 1,
    }
    forward = independent_strength_prior(a)
    reverse = independent_strength_prior({
        **a, "independent_elo_a": a["independent_elo_b"], "independent_elo_b": a["independent_elo_a"],
        "independent_glicko_a": a["independent_glicko_b"], "independent_glicko_b": a["independent_glicko_a"],
        "independent_rd_a": a["independent_rd_b"], "independent_rd_b": a["independent_rd_a"],
        "tour_rank_a": a["tour_rank_b"], "tour_rank_b": a["tour_rank_a"],
        "rolling_90d_summary_a": a["rolling_90d_summary_b"],
        "rolling_90d_summary_b": a["rolling_90d_summary_a"],
        "h2h_wins_a": a["h2h_wins_b"], "h2h_wins_b": a["h2h_wins_a"],
    })
    assert forward["probability"] > 0.5
    assert reverse["probability"] == pytest.approx(1.0 - forward["probability"])
    assert {row["name"] for row in forward["components"]} == {
        "elo", "glicko", "tour_rank", "rolling_90d", "head_to_head"
    }


def test_independent_baseline_priors_and_strength_shift_are_deterministic():
    pair = {"independent_metrics_available_a": False, "independent_metrics_available_b": False}
    assert independent_strength_prior(pair)["probability"] is None
    state = TennisState(sets_to_win=2)
    serve_a, serve_b, detail = apply_independent_strength_prior(
        (0.62, 0.62, 0.5), (0.62, 0.62, 0.5), 0.7, state, [200, 200, 200, 200]
    )
    assert detail["applied"]
    assert serve_a[0] > serve_b[0]
    vector = build_odds_free_baseline_feature_vector(state, {"match_format_known": False}, serve_a, serve_b)
    assert len(vector) == len(_MODULE.ODDS_FREE_BASELINE_FEATURE_NAMES)
    assert vector[_MODULE.ODDS_FREE_BASELINE_FEATURE_NAMES.index("sets_to_win")] == 0


def test_feature_availability_certificate_fails_closed_for_missing_or_future_sources():
    from datetime import datetime, timezone
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    complete = {source: as_of.isoformat() for source in ODDS_FREE_FEATURE_SOURCE_GROUPS}
    assert validate_odds_free_feature_availability(as_of, complete)["certified"] is True
    complete.pop("serve_metrics_b")
    complete["event_format"] = "2026-01-02T00:00:00+00:00"
    result = validate_odds_free_feature_availability(as_of, complete)
    assert result["certified"] is False
    assert result["missing_sources"] == ["serve_metrics_b"]
    assert result["future_sources"] == ["event_format"]
    result = validate_odds_free_feature_availability(
        as_of,
        {**complete, "serve_metrics_b": None, "event_format": as_of.isoformat()},
        contract_defaults=["serve_metrics_b"],
    )
    assert result["certified"] is True
    assert result["contract_defaulted_sources"] == ["serve_metrics_b"]


def test_historical_serve_marks_mixed_retrospective_rows():
    assert serve_retrospective_flags([
        {"retrospective": False}, {"retrospective": True},
    ]) == {
        "retrospective": True,
        "all_retrospective": False,
        "retrospective_count": 1,
    }


def test_surface_requires_its_own_timestamp_and_is_unknown_when_late():
    from datetime import datetime, timezone
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert point_in_time_surface({"surface": "clay"}, cutoff) == (
        "unknown", None, "missing_source_timestamp"
    )
    assert point_in_time_surface({
        "surface": "grass", "surface_available_at": "2026-01-02T00:00:00Z"
    }, cutoff) == ("unknown", "2026-01-02T00:00:00+00:00", "source_after_cutoff")
    assert point_in_time_surface({
        "surface": "hard", "surface_available_at": "2025-12-31T23:00:00Z"
    }, cutoff) == ("hard", "2025-12-31T23:00:00+00:00", "observed")


def test_best_of_requires_explicit_value_and_causal_observation_time():
    from datetime import datetime, timezone
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert point_in_time_best_of({"best_of": 3}, cutoff) == (
        None, None, "missing_source_timestamp"
    )
    assert point_in_time_best_of({
        "best_of": 5, "best_of_available_at": "2026-01-02T00:00:00Z"
    }, cutoff) == (None, "2026-01-02T00:00:00+00:00", "source_after_cutoff")
    assert point_in_time_best_of({
        "best_of": 3, "best_of_available_at": "2025-12-31T23:00:00Z"
    }, cutoff) == (3, "2025-12-31T23:00:00+00:00", "observed")
    assert point_in_time_best_of({"best_of": 4, "best_of_available_at": cutoff}, cutoff)[0] is None
    assert point_in_time_best_of({"best_of": 3.5, "best_of_available_at": cutoff}, cutoff)[0] is None


def test_unknown_format_does_not_create_match_win_probability():
    state = TennisState(sets_a=1, sets_b=1, sets_to_win=2)
    result = forecast(state, match_format_known=False)
    assert "match_a" not in result["probabilities"]
    assert result["scenarios"]["next_3_points"] == []
    assert result["scenarios"]["next_2_games"] == []


def test_unknown_format_parse_does_not_infer_best_of_from_score():
    state, error = parse_tennis_state({
        "stats": {"sets_a": 1, "sets_b": 1,
                  "set_lines": [{"games_a": 3, "games_b": 2, "live": True}]},
        "current_game": {"points_a": "15", "points_b": "0"},
    })
    assert error is None
    assert state.sets_to_win == 0


def test_live_continuation_after_two_sets_proves_best_of_five():
    from datetime import datetime, timezone

    observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state = SimpleNamespace(
        status="live", observed_at=observed_at,
        score_detail={},
        sport_state={"stats": {"sets_a": 2, "sets_b": 1}},
    )
    assert infer_live_best_of_from_continuation(state) == (5, observed_at.isoformat())
    state.sport_state["stats"]["sets_a"] = 1
    assert infer_live_best_of_from_continuation(state) == (None, None)
    state.status = "finished"
    state.sport_state["stats"]["sets_a"] = 2
    assert infer_live_best_of_from_continuation(state) == (None, None)


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
    state, error = parse_tennis_state(detail, sport, best_of=3)
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


def test_server_rotates_after_an_ordinary_completed_game():
    closed = _advance_point(TennisState(points_a=3, points_b=0, server="a"), "a")
    assert (closed.games_a, closed.games_b) == (1, 0)
    assert (closed.points_a, closed.points_b) == (0, 0)
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
    state, error = parse_tennis_state(detail, sport, best_of=3)
    assert error is None and state is not None
    assert (state.points_a, state.points_b, state.tiebreak) == (8, 7, True)

    malformed = {"stats": {"set_lines": [{"games_a": 7, "games_b": 7, "live": True}]}}
    state, error = parse_tennis_state({}, malformed, best_of=3)
    assert state is None and error == "UNSUPPORTED_SCORE"


def test_unknown_match_format_keeps_local_score_forecast_but_withholds_match_probability():
    state, error = parse_tennis_state(
        {"current_game": {"points_a": "0", "points_b": "0"}},
        {"stats": {"set_lines": [{"games_a": 0, "games_b": 0, "live": True}]}},
    )
    assert error is None and state is not None
    result = forecast(state, serve_a=(0.65, 0.62, 0.5), serve_b=(0.60, 0.62, 0.5), match_format_known=False)
    assert result["probabilities"]["next_point_a"] > 0
    assert result["probabilities"]["next_game_a"] > 0
    assert result["probabilities"]["current_set_a"] > 0
    assert "match_a" not in result["probabilities"]
    assert result["scenarios"] == {"next_3_points": [], "next_2_games": []}


def test_unknown_match_format_still_parses_a_mid_match_sets_score():
    state, error = parse_tennis_state(
        {"current_game": {"points_a": "15", "points_b": "0"}},
        {"stats": {"sets_a": 1, "sets_b": 1,
                    "set_lines": [{"games_a": 3, "games_b": 2, "live": True}]}},
    )
    assert error is None and state is not None
    assert (state.sets_a, state.sets_b) == (1, 1)


def test_fonbet_double_zero_points_parse_as_love():
    state, error = parse_tennis_state(
        {"current_game": {"points_a": "00", "points_b": "00"}},
        {"stats": {"set_lines": [{"games_a": 2, "games_b": 5, "live": True}]}},
    )
    assert error is None and state is not None
    assert (state.points_a, state.points_b) == (0, 0)


def test_unknown_match_format_rejects_an_impossible_sets_score():
    state, error = parse_tennis_state(
        {"current_game": {"points_a": "0", "points_b": "0"}},
        {"stats": {"sets_a": 3, "sets_b": 0,
                    "set_lines": [{"games_a": 1, "games_b": 0, "live": True}]}},
    )
    assert state is None and error == "UNSUPPORTED_SCORE"


def test_best_of_five_format_uses_three_sets_to_win():
    state, error = parse_tennis_state(
        {"current_game": {"points_a": "0", "points_b": "0"}},
        {"stats": {"sets_a": 2, "sets_b": 1,
                   "set_lines": [{"games_a": 1, "games_b": 0, "live": True}]}},
        best_of=5,
    )
    assert error is None
    assert state.sets_to_win == 3
    assert (state.sets_a, state.sets_b) == (2, 1)


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


def test_unknown_server_scenarios_keep_every_terminal_state_and_normalize_mass():
    states = [
        TennisState(points_a=3, points_b=3, server=None),
        TennisState(games_a=6, games_b=6, points_a=6, points_b=6,
                    server=None, tiebreak=True, tiebreak_server=None),
    ]
    for state in states:
        result = forecast(state, serve_a=(0.62, 0.62, 0.5), serve_b=(0.62, 0.62, 0.5))
        for horizon in ("next_3_points", "next_2_games"):
            scenarios = result["scenarios"][horizon]
            assert scenarios
            assert abs(sum(item["probability"] for item in scenarios) - 1.0) < 1e-12
            assert all(item["probability"] >= 0.0 for item in scenarios)
    # Unknown service assignment at deuce creates more than eight distinct
    # score/server endpoints; none may be silently discarded.
    deuce = forecast(states[0], serve_a=(0.62, 0.62, 0.5), serve_b=(0.62, 0.62, 0.5))
    assert len(deuce["scenarios"]["next_3_points"]) > 8


def test_completed_match_is_an_absorbing_state_for_both_horizons():
    state = TennisState(sets_a=2, games_a=4, games_b=3, points_a=2, server="a", sets_to_win=2)
    result = forecast(state, serve_a=(0.65, 0.62, 0.5), serve_b=(0.60, 0.62, 0.5))
    assert result["probabilities"]["match_a"] == 1.0
    for horizon in ("next_3_points", "next_2_games"):
        scenarios = result["scenarios"][horizon]
        assert sum(item["probability"] for item in scenarios) == pytest.approx(1.0)
        assert all(item["state"] == state.__dict__ for item in scenarios)


def test_event_attribute_resolver_uses_latest_version_at_cutoff_and_never_future_value():
    cutoff = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    rows = [
        SimpleNamespace(attribute_name="best_of", available_at=datetime(2026, 10, 26, 10, tzinfo=timezone.utc), id="1"),
        SimpleNamespace(attribute_name="best_of", available_at=datetime(2026, 10, 26, 13, tzinfo=timezone.utc), id="2"),
        SimpleNamespace(attribute_name="surface", available_at=datetime(2026, 10, 26, 11, tzinfo=timezone.utc), id="3"),
    ]

    selected = resolve_event_attribute_observations(rows, cutoff)

    assert selected == {"best_of": rows[0], "surface": rows[2]}


def test_name_only_serve_logs_are_not_used_without_identity_provenance():
    rates_a, rates_b, provenance = player_serve_rate_snapshot(
        object(), "Player A", "Player B", datetime(2026, 10, 26, tzinfo=timezone.utc), "clay"
    )

    assert rates_a == (0.62, 0.62, 0.0)
    assert rates_b == (0.62, 0.62, 0.0)
    assert provenance["a"]["missingness"] == "serve_identity_provenance_missing"
    assert provenance["b"]["missingness"] == "serve_identity_provenance_missing"
