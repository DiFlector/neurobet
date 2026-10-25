from datetime import datetime, timedelta, timezone

from tennis_adapter.player_metrics import aggregate_results, metrics_as_of


def _result(days_ago: int, won: bool, match_id: str) -> dict:
    return {
        "source_match_id": match_id,
        "played_at": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=days_ago),
        "won": won,
        "surface": "hard",
        "tour_level": "atp",
        "sets_played": 3,
    }


def test_aggregate_keeps_denominators_and_unknown_serve_volume():
    metrics = aggregate_results([_result(0, True, "a"), _result(1, False, "b")])

    assert metrics["overall"] == {"matches": 2, "wins": 1, "losses": 1}
    assert metrics["by_surface"]["hard"]["matches"] == 2
    assert metrics["recent"]["last_10"]["win_rate"] == 0.5
    assert metrics["serve"]["unknown_volume_matches"] == 2
    assert metrics["glicko"]["rd"] < 350


def test_result_without_serve_stats_remains_usable_history_with_unknown_serve_volume():
    metrics = aggregate_results([_result(0, True, "tml-result-no-stats")])

    assert metrics["overall"]["matches"] == 1
    assert metrics["overall"]["wins"] == 1
    assert metrics["serve"]["points"] == 0
    assert metrics["serve"]["points_won"] == 0
    assert metrics["serve"]["unknown_volume_matches"] == 1
    assert metrics["coverage"] == {"match_history": "available", "serve_volume_known": False}


def test_rolling_windows_expire_without_loading_match_archive():
    metrics = aggregate_results([_result(-100, True, "old"), _result(-2, False, "recent")])

    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    current = metrics_as_of(metrics, at)

    assert current["overall"]["matches"] == 2
    assert current["recent"]["days_90"]["matches"] == 1
    assert current["recent"]["days_90"]["wins"] == 0
    assert current["load"]["matches_7d"] == 1


def test_as_of_cutoff_hides_results_not_yet_played():
    metrics = aggregate_results([_result(0, True, "a"), _result(-1, False, "b")])
    current = metrics_as_of(metrics, datetime(2025, 12, 31, 12, tzinfo=timezone.utc))

    assert current["recent"]["last_10"]["matches"] == 1
    assert current["recent"]["last_10"]["wins"] == 0


def test_last_twenty_results_are_retained_beyond_one_year():
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    metrics = aggregate_results({
        "source_match_id": str(index),
        "played_at": start + timedelta(days=30 * index),
        "won": bool(index % 2),
    } for index in range(30))
    current = metrics_as_of(metrics, start + timedelta(days=30 * 29))

    assert current["recent"]["last_20"]["matches"] == 20
    assert len(current["recent_results"]) >= 20
