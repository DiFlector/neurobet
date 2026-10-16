"""Set lines, the score tape, and the match verdict that waits when sets disagree."""

import importlib.util
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

for path in (
    "/srv/neurobet/packages/contracts/src",
    "/srv/neurobet/packages/features/src",
    "/srv/neurobet/services/neural",
    "/srv/neurobet/packages/sports-core/src",
    "/srv/neurobet/sports/tennis/src",
):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.dataset import MatchSnapshots, stack_matches, thin_match
from app.set_match import match_verdict, set_is_committable
from features.tennis import TennisFeatureBuilder
from tennis_adapter.adapter import closed_set_winners, final_set_winners, set_winner_from_games, winners_from_score_text

_spec = importlib.util.spec_from_file_location(
    "fonbet_parser",
    "/srv/neurobet/services/collector/app/parser.py",
)
_parser = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parser)
FonbetLiveTennisParser = _parser.FonbetLiveTennisParser


def test_set_winner_and_child_market():
    assert set_winner_from_games(6, 4) == "player_a"
    assert set_winner_from_games(6, 5) is None
    assert set_winner_from_games(3, 2) is None
    winners = closed_set_winners([
        {"games_a": 6, "games_b": 4, "live": False},
        {"games_a": 2, "games_b": 1, "live": True},
    ])
    assert winners == {"1": "player_a"}
    feed = {
        "sports": [{"id": 4, "name": "Теннис"}],
        "events": [
            {"id": 10, "place": "live", "sportId": 4, "team1": "А", "team2": "Б"},
            {"id": 11, "parentId": 10, "place": "live", "sportId": 4, "name": "1-й сет", "team1": "А", "team2": "Б"},
        ],
        "customFactors": [
            {"e": 10, "factors": [{"f": 921, "v": 1.8}, {"f": 923, "v": 2.0}]},
            {"e": 11, "factors": [{"f": 921, "v": 1.7}, {"f": 923, "v": 2.1}]},
        ],
    }
    cards = FonbetLiveTennisParser.from_line_feed(feed)["events"]
    assert len(cards) == 1
    parsed = FonbetLiveTennisParser.parse_events_feed({"events": cards})
    markets = {item["market_type"]: item for item in parsed[0]["markets"]}
    assert "match_winner" in markets
    assert markets["set_winner"]["line_value"] == 1
    assert markets["set_winner"]["selections"][0]["odds"] == 1.7


def test_score_tape_counts_each_point_and_parentheses():
    def state(period, games_a, games_b, points_a, points_b, server, lines):
        return SimpleNamespace(
            current_period=period,
            score_detail={"current_game": {"points_a": points_a, "points_b": points_b}},
            sport_state={"server": server, "stats": {"set_lines": lines}},
        )

    lines = [{"games_a": games_a, "games_b": games_b, "live": True} for games_a, games_b in [(0, 0), (0, 0)]]
    states = [
        state(1, 0, 0, "0", "0", "player_a", [{"games_a": 0, "games_b": 0, "live": True}]),
        state(1, 0, 0, "15", "0", "player_a", [{"games_a": 0, "games_b": 0, "live": True}]),
        state(1, 0, 0, "15", "15", "player_a", [{"games_a": 0, "games_b": 0, "live": True}]),
    ]
    tape = TennisFeatureBuilder()._score_tape(states)
    assert tape["tape_points_a"] == 1
    assert tape["tape_points_b"] == 1
    assert tape["tape_serve_won_a"] == 1
    assert tape["tape_return_won_b"] == 1
    assert tape["paren_set_1_diff"] == 0


def test_finished_match_closes_last_live_set_and_skips_unplayed():
    lines = [
        {"games_a": 6, "games_b": 3, "live": False},
        {"games_a": 6, "games_b": 4, "live": True},
        {"games_a": 0, "games_b": 0, "live": True},
    ]
    assert closed_set_winners(lines) == {"1": "player_a"}
    assert final_set_winners(lines) == {"1": "player_a", "2": "player_a"}
    assert winners_from_score_text("0:2 (3-6 4-6)") == {"1": "player_b", "2": "player_b"}
    assert set_is_committable(2, 1, {}, 3) is False
    assert set_is_committable(3, 2, {1: "player_a", 2: "player_a"}, 3) is False
    assert set_is_committable(2, 2, {1: "player_a"}, 3) is True


def test_match_waits_when_sets_disagree_and_uses_a_missed_set():
    assert match_verdict({1: "player_a"}, {2: None}, False, "player_a", 0.05) is None
    assert match_verdict({1: "player_a"}, {2: "player_a"}, False, "player_a", 0.05) == "player_a"
    assert match_verdict({1: "player_a"}, {2: "player_b"}, False, "player_a", 0.05) is None
    assert match_verdict({}, {}, True, "player_a", 0.2) is None
    assert match_verdict({1: "player_a", 2: "player_a"}, {}, False, "player_a", 0.03) == "player_a"


def test_set_rows_keep_their_own_label():
    match = MatchSnapshots(
        event_id="1",
        started_at=datetime.now(timezone.utc),
        label=1,
        rows=[[1.0], [1.0], [0.0], [0.0]],
        observed_at=[1, 2, 3, 4],
        row_labels=[1, 1, 0, 0],
        row_markets=["match_winner", "match_winner", "set_winner:1", "set_winner:1"],
    )
    _x, labels, _w = stack_matches([thin_match(match, limit=2)])
    assert labels.tolist() == [1, 1, 0, 0]


if __name__ == "__main__":
    test_set_winner_and_child_market()
    test_finished_match_closes_last_live_set_and_skips_unplayed()
    test_score_tape_counts_each_point_and_parentheses()
    test_match_waits_when_sets_disagree_and_uses_a_missed_set()
    test_set_rows_keep_their_own_label()
    print("ok")
