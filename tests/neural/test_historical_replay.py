"""Historical replay uses the live confirmation rule and an isolated bankroll."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from contracts import FeatureVector

from app.confirmation import next_watch_action
from app.replay.bankroll import ReplayBankroll
from app.replay.engine import ReplayRunner


def test_two_snapshots_commit_and_a_repeat_does_not():
    first_action, first = next_watch_action(None, "t1", "player_a", True, "", required=2)
    second_action, _second = next_watch_action(first, "t1", "player_a", True, "", required=2)
    third_action, third = next_watch_action(first, "t2", "player_a", True, "", required=2)
    assert first_action == "watch"
    assert second_action == "unchanged"
    assert third_action == "commit"
    assert third["agree_count"] == 2


def test_bankruptcy_restores_the_initial_balance():
    book = ReplayBankroll(200, 100, 100)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert book.ensure_solvent(now) is False
    placed = book.place_bet("b1", "e", "player_a", now, 2.0, 100, 0.6, 0.2)
    book.settle_bet(placed.bet_id, "player_b", now)
    assert book.current_balance == 100
    placed = book.place_bet("b2", "e2", "player_a", now, 2.0, 100, 0.6, 0.2)
    book.settle_bet(placed.bet_id, "player_b", now)
    assert book.current_balance == 0
    assert book.ensure_solvent(now) is True
    assert book.bankruptcies == 1
    assert book.current_balance == 200


class _Quote:
    def __init__(self, observed_at, price_a, price_b):
        self.observed_at = observed_at
        self.market_type = "match_winner"
        self.outcome = None
        self.odds = None
        self.line_value = None
        self.status = "active"
        self._a = price_a
        self._b = price_b


def _quotes(moment, price_a, price_b):
    first = _Quote(moment, price_a, price_b)
    first.outcome = "player_a"
    first.odds = price_a
    second = _Quote(moment, price_a, price_b)
    second.outcome = "player_b"
    second.odds = price_b
    return [first, second]


class _Builder:
    def build_features(self, event_id, cutoff_time, event_snapshots, odds_snapshots):
        for row in list(event_snapshots) + list(odds_snapshots):
            assert row.observed_at <= cutoff_time
        return FeatureVector(
            event_id=str(event_id),
            sport_code="tennis",
            feature_cutoff_timestamp=cutoff_time,
            features={"context_side_a": 0.5},
        )


class _Predictor:
    checkpoint_data = {
        "version_tag": "tennis_v1.0.0",
        "model_name": "tennis_logistic_regression",
        "feature_names": [],
    }

    def predict(self, feature_vector, odds_a, odds_b, is_shadow=False, min_edge_threshold=0.02, peer_probabilities=None):
        probability = 0.7
        edge = probability * float(odds_a) - 1.0
        side = SimpleNamespace(
            outcome="player_a",
            model_probability=probability,
            bookmaker_odds=float(odds_a),
            fair_odds=1.43,
            edge=edge,
            expected_value=0.2,
            confidence=0.4,
        )
        return SimpleNamespace(
            outcomes=[side],
            best_outcome="player_a" if edge >= min_edge_threshold else None,
            has_positive_edge=edge >= min_edge_threshold,
            market="match_winner",
            model_version="tennis_v1.0.0",
        )


def _match(event_id, start, winner):
    later = start + timedelta(hours=3)
    states = [SimpleNamespace(observed_at=start), SimpleNamespace(observed_at=start + timedelta(minutes=5))]
    odds = _quotes(start, 1.70, 2.20) + _quotes(start + timedelta(minutes=5), 1.70, 2.20)
    odds += _quotes(later, 9.90, 1.05)
    return SimpleNamespace(
        event_id=event_id,
        started_at=start,
        states=states,
        odds=odds,
        decisions=[],
        history_features=None,
        set_winners={},
        event=SimpleNamespace(
            current_score={"winner": winner},
            status="finished",
            metadata_json={},
            finished_at=later,
        ),
    )


def _config(**overrides):
    config = {
        "min_edge": 0.02,
        "min_probability": 0.40,
        "odds_gate_mode": "live",
        "min_odds": 1.30,
        "max_odds": 2.40,
        "confirmations": 2,
        "max_odds_staleness_sec": 15,
        "execution_latency_sec": 0,
        "allow_slippage": True,
        "initial_bankroll": 5000,
        "fixed_stake": 100,
        "bankruptcy_threshold": 100,
    }
    config.update(overrides)
    return config


def test_replay_places_one_fixed_stake_without_future_prices():
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    seen = []
    report = ReplayRunner(_Predictor(), _config(), "tennis").run(
        [_match("m1", start, "player_a")],
        _Builder(),
        on_match=lambda done, total: seen.append((done, total)),
    )
    assert seen == [(1, 1)]
    assert report["bets"] == 1
    assert report["wins"] == 1
    assert report["bet_records"][0]["odds"] == 1.70
    assert report["bet_records"][0]["stake"] == 100
    assert report["final_balance"] == 5070
    assert report["roi"] == 0.7
    assert "bets" not in report["gates_applied"]


class _V2Predictor(_Predictor):
    checkpoint_data = {
        "version_tag": "tennis_v2.0.0",
        "model_name": "tennis_logistic_regression",
        "feature_names": [],
    }


def _live_state(moment, lines, status="live"):
    return SimpleNamespace(
        observed_at=moment,
        status=status,
        current_period=1,
        score_detail={},
        sport_state={"stats": {"set_lines": lines}},
    )


def test_replay_uses_sets_closed_on_the_snapshot():
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    later = start + timedelta(minutes=5)
    leaked = _match("m-leak", start, "player_a")
    leaked.set_winners = {"1": "player_a", "2": "player_a"}
    leaked.states = [
        _live_state(start, [{"games_a": 0, "games_b": 0, "live": True}]),
        _live_state(later, [{"games_a": 1, "games_b": 0, "live": True}]),
    ]
    leaked.odds = _quotes(start, 1.70, 2.20) + _quotes(later, 1.70, 2.20)
    hidden = ReplayRunner(_V2Predictor(), _config(), "tennis").run([leaked], _Builder())
    assert hidden["bets"] == 0

    closed_lines = [
        {"games_a": 6, "games_b": 3, "live": False},
        {"games_a": 6, "games_b": 4, "live": False},
    ]
    visible = _match("m-closed", start, "player_a")
    visible.set_winners = {"1": "player_b", "2": "player_b"}
    visible.states = [
        _live_state(start, closed_lines, status="finished"),
        _live_state(later, closed_lines, status="finished"),
    ]
    visible.odds = _quotes(start, 1.70, 2.20) + _quotes(later, 1.70, 2.20)
    shown = ReplayRunner(_V2Predictor(), _config(), "tennis").run([visible], _Builder())
    assert shown["bets"] == 1
    assert shown["bet_records"][0]["market"] == "match_winner"
    assert shown["bet_records"][0]["outcome"] == "player_a"


def _set_quotes(moment, number, price_a, price_b):
    rows = []
    for outcome, price in (("player_a", price_a), ("player_b", price_b)):
        row = _Quote(moment, price_a, price_b)
        row.market_type = "set_winner"
        row.line_value = number
        row.outcome = outcome
        row.odds = price
        rows.append(row)
    return rows


class _SecondSetBuilder(_Builder):
    def build_features(self, event_id, cutoff_time, event_snapshots, odds_snapshots):
        vector = super().build_features(event_id, cutoff_time, event_snapshots, odds_snapshots)
        vector.features["current_set"] = 2.0
        return vector


def test_replay_watches_the_first_set_and_stakes_the_second():
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    later = start + timedelta(minutes=5)
    opening = _match("m-open", start, "player_a")
    opening.states = [
        _live_state(start, [{"games_a": 0, "games_b": 0, "live": True}]),
        _live_state(later, [{"games_a": 0, "games_b": 0, "live": True}]),
    ]
    opening.odds = (
        _quotes(start, 1.70, 2.20)
        + _set_quotes(start, 1, 1.70, 2.20)
        + _quotes(later, 1.70, 2.20)
        + _set_quotes(later, 1, 1.70, 2.20)
    )
    watched = ReplayRunner(_V2Predictor(), _config(), "tennis").run([opening], _Builder())
    assert watched["bets"] == 0

    lines = [
        {"games_a": 6, "games_b": 3, "live": False},
        {"games_a": 0, "games_b": 0, "live": True},
    ]
    second = _match("m-set2", start, "player_a")
    second.states = [_live_state(start, lines), _live_state(later, lines)]
    for state in second.states:
        state.current_period = 2
    second.odds = (
        _quotes(start, 1.70, 2.20)
        + _set_quotes(start, 2, 1.70, 2.20)
        + _quotes(later, 1.70, 2.20)
        + _set_quotes(later, 2, 1.70, 2.20)
    )
    played = ReplayRunner(_V2Predictor(), _config(), "tennis").run([second], _SecondSetBuilder())
    markets = [row["market"] for row in played["bet_records"]]
    assert "set_winner:1" not in markets
    assert "set_winner:2" in markets


class _MatchModel(_V2Predictor):
    checkpoint_data = {
        "version_tag": "tennis_v2.3.0",
        "model_name": "tennis_logistic_regression",
        "feature_names": [],
    }


class _SetModel(_Predictor):
    checkpoint_data = {
        "version_tag": "tennis_v1.0.0",
        "model_name": "tennis_composite",
        "kind": "composite",
        "feature_names": [],
    }


def test_assigned_models_stake_only_their_market():
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    later = start + timedelta(minutes=5)
    lines = [
        {"games_a": 6, "games_b": 3, "live": False},
        {"games_a": 0, "games_b": 0, "live": True},
    ]
    second = _match("m-role", start, "player_a")
    second.states = [_live_state(start, lines), _live_state(later, lines)]
    for state in second.states:
        state.current_period = 2
    second.odds = (
        _quotes(start, 1.70, 2.20)
        + _set_quotes(start, 2, 1.70, 2.20)
        + _quotes(later, 1.70, 2.20)
        + _set_quotes(later, 2, 1.70, 2.20)
    )
    config = _config(markets=["match_winner", "set_winner"])
    logistic = ReplayRunner(_MatchModel(), config, "tennis").run([second], _SecondSetBuilder())
    logistic_markets = {row["market"] for row in logistic["bet_records"]}
    assert not any(str(market).startswith("set_winner") for market in logistic_markets)

    composite = ReplayRunner(_SetModel(), config, "tennis").run([second], _SecondSetBuilder())
    composite_markets = [row["market"] for row in composite["bet_records"]]
    assert "set_winner:2" in composite_markets
    assert "match_winner" not in composite_markets


def test_three_losses_count_one_bankruptcy_and_continue():
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    matches = [_match(f"m{index}", start + timedelta(days=index), "player_b") for index in range(3)]
    report = ReplayRunner(
        _Predictor(),
        _config(initial_bankroll=200, fixed_stake=100, bankruptcy_threshold=100),
        "tennis",
    ).run(matches, _Builder())
    assert report["bets"] == 3
    assert report["losses"] == 3
    assert report["bankruptcies"] == 1
    assert report["final_balance"] == 100
    assert report["net_pnl"] == -300


if __name__ == "__main__":
    test_two_snapshots_commit_and_a_repeat_does_not()
    test_bankruptcy_restores_the_initial_balance()
    test_replay_places_one_fixed_stake_without_future_prices()
    test_replay_uses_sets_closed_on_the_snapshot()
    test_replay_watches_the_first_set_and_stakes_the_second()
    test_assigned_models_stake_only_their_market()
    test_three_losses_count_one_bankruptcy_and_continue()
    print("historical replay tests passed")
