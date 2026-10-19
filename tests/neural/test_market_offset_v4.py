"""Focused contract and inference checks for the tennis market-offset v4 head."""

import joblib
import numpy as np
from datetime import datetime, timedelta, timezone

from features.selection import SELECTED_TENNIS_FEATURES_V4, select_tennis_v4_features
from app.dataset import MatchSnapshots
from app.models import TennisMarketOffsetEstimator
from app.dataset import thin_match
from tennis_adapter.policy import market_limits_from_mapping, resolve_replay_policy


def _match(index: int) -> MatchSnapshots:
    rows = []
    for is_set in (0.0, 1.0):
        row = np.zeros(len(SELECTED_TENNIS_FEATURES_V4), dtype=float)
        row[SELECTED_TENNIS_FEATURES_V4.index("no_vig_prob_player_a")] = 0.45 + 0.02 * (index % 5)
        row[SELECTED_TENNIS_FEATURES_V4.index("market_is_set")] = is_set
        rows.append(row.tolist())
    label = index % 2
    return MatchSnapshots(
        event_id=str(index),
        started_at=np.datetime64("2026-01-01") + np.timedelta64(index, "D"),
        label=label,
        rows=rows,
        observed_at=[
            np.datetime64("2026-01-01T00:00") + np.timedelta64(index * 2 + offset, "D")
            for offset in (0, 1)
        ],
        row_labels=[label, label],
        row_markets=["match_winner", "set_winner:2"],
    )


def test_v4_contract_is_exact_and_strict():
    assert len(SELECTED_TENNIS_FEATURES_V4) == 23
    assert SELECTED_TENNIS_FEATURES_V4[-1] == "market_is_set"
    assert select_tennis_v4_features({name: 0.0 for name in SELECTED_TENNIS_FEATURES_V4})["market_is_set"] == 0.0
    try:
        select_tennis_v4_features({})
    except ValueError as exc:
        assert "market_is_set" in str(exc)
    else:
        raise AssertionError("incomplete v4 features must be rejected")


def test_v4_market_heads_predict_and_survive_serialization(tmp_path):
    estimator = TennisMarketOffsetEstimator(n_estimators=8, early_stopping_rounds=2, n_jobs=1)
    estimator.fit_matches([_match(index) for index in range(30)], SELECTED_TENNIS_FEATURES_V4)
    matrix = np.asarray([_match(2).rows[0], _match(2).rows[1]])
    probabilities = estimator.predict_proba(matrix)
    assert probabilities.shape == (2, 2)
    assert np.allclose(probabilities.sum(axis=1), 1.0)
    assert set(estimator.models_) == {"match", "set"}

    path = tmp_path / "tennis_v4.joblib"
    joblib.dump(estimator, path)
    loaded = joblib.load(path)
    assert np.allclose(loaded.predict_proba(matrix), probabilities)


def test_training_thinning_is_match_wide_and_keeps_market_quotes():
    match = _match(1)
    rows = [np.asarray(match.rows[0]).tolist() for _ in range(30)] + [np.asarray(match.rows[1]).tolist() for _ in range(30)]
    match.rows = rows
    match.row_markets = ["match_winner"] * 30 + ["set_winner:2"] * 30
    match.row_labels = [1] * 60
    match.observed_at = [datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=i) for i in range(60)]
    match.row_odds_a = [1.8] * 30 + [1.7] * 30
    match.row_odds_b = [2.0] * 30 + [2.1] * 30
    match.result_available_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    result = thin_match(match, limit=12)
    assert len(result.rows) == len(result.row_odds_a) == len(result.row_odds_b) == 12
    assert set(result.row_markets) == {"match_winner", "set_winner:2"}
    assert sum(item == "match_winner" for item in result.row_markets) <= 6


def test_v4_result_availability_split_and_replay_policy_inheritance():
    matches = [_match(index) for index in range(20)]
    for index, match in enumerate(matches):
        match.result_available_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)
        # Deliberately overlap every observation timeline: result availability is
        # the temporal boundary for whole-match training windows.
        match.observed_at = [datetime(2026, 1, 1, tzinfo=timezone.utc)] * len(match.rows)
    train, early, calibration = TennisMarketOffsetEstimator._split_matches(matches)
    assert (len(train), len(early), len(calibration)) == (12, 4, 4)
    assert max(item.result_available_at for item in train) < min(item.result_available_at for item in early)
    policy = resolve_replay_policy({})
    assert policy["match_min_edge"] == policy["set_min_edge"] == 0.04
    assert policy["match_confirmations"] == policy["set_confirmations"] == 3
    assert market_limits_from_mapping("match_winner", {"min_edge": 0.03})["min_edge"] == 0.03
    assert market_limits_from_mapping("set_winner", {"match_min_edge": 0.05})["min_edge"] == 0.04


if __name__ == "__main__":
    test_v4_contract_is_exact_and_strict()
    test_v4_market_heads_predict_and_survive_serialization(__import__("pathlib").Path("/tmp"))
    print("test_market_offset_v4: ALL PASSED")
