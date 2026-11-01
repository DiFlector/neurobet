from datetime import datetime, timezone
import importlib.util
import sys


SPEC = importlib.util.spec_from_file_location(
    "tennis_independent_metrics", "sports/tennis/src/tennis_adapter/independent_metrics.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_replay_is_pit_and_ignores_future_or_not_yet_available_results():
    cutoff = datetime(2025, 1, 10, tzinfo=timezone.utc)
    rows = [
        {"canonical_match_id": "m1", "winner_id": "a", "loser_id": "b",
         "played_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
         "available_at": datetime(2025, 1, 2, tzinfo=timezone.utc)},
        {"canonical_match_id": "m2", "winner_id": "b", "loser_id": "a",
         "played_at": datetime(2025, 1, 11, tzinfo=timezone.utc),
         "available_at": datetime(2025, 1, 3, tzinfo=timezone.utc)},
        {"canonical_match_id": "m3", "winner_id": "b", "loser_id": "a",
         "played_at": datetime(2025, 1, 3, tzinfo=timezone.utc),
         "available_at": datetime(2025, 1, 11, tzinfo=timezone.utc)},
    ]
    metrics = MODULE.replay_independent_metrics(rows, cutoff)
    assert metrics["a"]["overall"]["matches"] == 1
    assert metrics["a"]["overall"]["wins"] == 1
    assert metrics["b"]["overall"]["losses"] == 1


def test_both_players_update_from_same_pre_match_ratings_and_neutral_serve_prior():
    cutoff = datetime(2025, 1, 10, tzinfo=timezone.utc)
    row = {
        "canonical_match_id": "m1", "winner_id": "a", "loser_id": "b",
        "played_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "available_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "serve_points_a": 100, "serve_points_won_a": 62,
        "serve_points_b": 100, "serve_points_won_b": 62,
    }
    result = MODULE.replay_independent_metrics([row], cutoff)
    assert result["a"]["elo"]["overall"] == 1516
    assert result["b"]["elo"]["overall"] == 1484
    assert result["a"]["serve"]["adjusted_rate"] == 0.62
    assert result["a"]["return"]["adjusted_rate"] == 0.38


def test_result_correction_a_b_a_is_point_in_time_and_replays_dependent_opponents():
    played = datetime(2025, 1, 1, tzinfo=timezone.utc)
    versions = [
        {"canonical_match_id": "m1", "winner_id": "a", "loser_id": "b", "played_at": played,
         "available_at": datetime(2025, 1, 2, tzinfo=timezone.utc)},
        {"canonical_match_id": "m1", "winner_id": "b", "loser_id": "a", "played_at": played,
         "available_at": datetime(2025, 1, 4, tzinfo=timezone.utc)},
        {"canonical_match_id": "m1", "winner_id": "a", "loser_id": "b", "played_at": played,
         "available_at": datetime(2025, 1, 6, tzinfo=timezone.utc)},
        {"canonical_match_id": "m2", "winner_id": "c", "loser_id": "a",
         "played_at": datetime(2025, 1, 3, tzinfo=timezone.utc),
         "available_at": datetime(2025, 1, 3, tzinfo=timezone.utc)},
    ]
    before = MODULE.replay_independent_metrics(versions, datetime(2025, 1, 3, tzinfo=timezone.utc))
    corrected = MODULE.replay_independent_metrics(versions, datetime(2025, 1, 5, tzinfo=timezone.utc))
    reverted = MODULE.replay_independent_metrics(versions, datetime(2025, 1, 7, tzinfo=timezone.utc))
    assert before["a"]["overall"]["wins"] == 1
    assert corrected["a"]["overall"]["wins"] == 0
    assert corrected["c"]["elo"]["overall"] != before["c"]["elo"]["overall"]
    assert reverted["a"]["overall"]["wins"] == 1
    assert before["a"]["elo"]["overall"] == reverted["a"]["elo"]["overall"]


def test_streamed_canonical_replay_matches_version_deduplicating_replay():
    rows = [
        {"canonical_match_id": "m1", "winner_id": "a", "loser_id": "b",
         "played_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
         "available_at": datetime(2025, 1, 2, tzinfo=timezone.utc)},
        {"canonical_match_id": "m2", "winner_id": "b", "loser_id": "c",
         "played_at": datetime(2025, 1, 3, tzinfo=timezone.utc),
         "available_at": datetime(2025, 1, 3, tzinfo=timezone.utc)},
    ]
    cutoff = datetime(2025, 1, 10, tzinfo=timezone.utc)
    assert MODULE.replay_independent_metrics(rows, cutoff) == MODULE.replay_independent_metrics(
        iter(rows), cutoff, canonical_sorted=True
    )


def test_head_to_head_is_reciprocal_and_uses_canonical_opponent_ids():
    rows = [
        {"canonical_match_id": "m1", "winner_id": "a", "loser_id": "b",
         "played_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
         "available_at": datetime(2025, 1, 2, tzinfo=timezone.utc)},
        {"canonical_match_id": "m2", "winner_id": "b", "loser_id": "a",
         "played_at": datetime(2025, 2, 1, tzinfo=timezone.utc),
         "available_at": datetime(2025, 2, 2, tzinfo=timezone.utc)},
    ]
    metrics = MODULE.replay_independent_metrics(rows, datetime(2025, 3, 1, tzinfo=timezone.utc))
    assert metrics["a"]["head_to_head"]["b"] == {
        "matches": 2, "wins": 1, "losses": 1, "last_meeting_at": "2025-02-01T00:00:00+00:00",
        "win_rate": 0.5,
    }
    assert metrics["b"]["head_to_head"]["a"]["matches"] == 2
    assert metrics["b"]["head_to_head"]["a"]["wins"] == 1
    cutoff_metrics = MODULE.replay_independent_metrics(rows, datetime(2025, 1, 15, tzinfo=timezone.utc))
    assert cutoff_metrics["a"]["head_to_head"]["b"]["matches"] == 1
    target_metrics = MODULE.replay_independent_metrics(
        rows, datetime(2025, 3, 1, tzinfo=timezone.utc), head_to_head_player_ids={"a"}
    )
    assert target_metrics["a"]["head_to_head"] == {}
    assert target_metrics["b"]["head_to_head"] == {}
