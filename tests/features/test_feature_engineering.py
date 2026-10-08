"""Comprehensive test suite for Phase 8: Feature Engineering and Zero Data Leakage."""

import sys
import uuid
from datetime import datetime, timedelta, timezone

# Path configuration
paths = [
    "/srv/neurobet/packages/contracts/src",
    "/srv/neurobet/packages/features/src",
    "/srv/neurobet/packages/db/src",
    "/app/packages/contracts/src",
    "/app/packages/features/src",
    "/app/packages/db/src",
    "/app",
]
for p in paths:
    if p not in sys.path:
        sys.path.insert(0, p)

from contracts import FeatureVector
from features import (
    ROLLING_WINDOWS,
    TennisFeatureBuilder,
    calculate_line_drift,
    calculate_no_vig_probabilities,
    calculate_odds_delta,
    calculate_odds_velocity,
    calculate_volatility,
    registry,
)


class MockSnapshot:
    def __init__(self, observed_at, **kwargs):
        self.observed_at = observed_at
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_base_math_calculations():
    """Verify odds delta, velocity, volatility, drift, and no-vig probability."""
    # Odds delta & velocity
    delta = calculate_odds_delta(current_odds=2.00, past_odds=1.80)
    assert delta == 0.20
    velocity = calculate_odds_velocity(delta=0.20, window_seconds=10)
    assert velocity == 0.02

    # Volatility
    vol = calculate_volatility([1.80, 1.85, 1.90, 1.95, 2.00])
    assert vol > 0.07

    # Drift
    assert calculate_line_drift(delta=0.05) == 1.0
    assert calculate_line_drift(delta=-0.05) == -1.0
    assert calculate_line_drift(delta=0.005) == 0.0

    # No-vig probability
    # Equal odds 2.0 and 2.0 -> no vig prob 0.5 and 0.5, margin = 0.0
    prob_a, prob_b, margin = calculate_no_vig_probabilities(2.0, 2.0)
    assert prob_a == 0.5
    assert prob_b == 0.5
    assert margin == 0.0

    # Typical bookmaker odds 1.90 vs 1.90 -> implied = 0.5263 + 0.5263 = 1.0526, margin ~ 5.26%
    p1, p2, m = calculate_no_vig_probabilities(1.90, 1.90)
    assert p1 == 0.5
    assert p2 == 0.5
    assert m > 0.05


def test_tennis_feature_builder_context_and_windows():
    """Verify full feature extraction including score differential and rolling windows."""
    builder = TennisFeatureBuilder()
    ev_id = str(uuid.uuid4())
    t0 = datetime(2026, 10, 8, 14, 0, 0, tzinfo=timezone.utc)
    cutoff = t0 + timedelta(seconds=65)

    # Historical state snapshots
    states = [
        MockSnapshot(
            observed_at=t0,
            current_period=1,
            status="live",
            score="0-0",
            score_detail={"current_game_a": 0, "current_game_b": 0, "points_a": "0", "points_b": "0", "sets_a": 0, "sets_b": 0},
            sport_state={"server": "player_a", "is_break_point": False},
            match_clock_seconds=0,
        ),
        MockSnapshot(
            observed_at=t0 + timedelta(seconds=60),
            current_period=1,
            status="live",
            score="3-2",
            score_detail={"current_game_a": 3, "current_game_b": 2, "points_a": "40", "points_b": "30", "sets_a": 0, "sets_b": 0},
            sport_state={"server": "player_b", "is_break_point": True},
            match_clock_seconds=360,
        ),
    ]

    # Historical odds snapshots
    odds = [
        MockSnapshot(observed_at=t0, outcome="player_a", odds=1.85),
        MockSnapshot(observed_at=t0, outcome="player_b", odds=1.95),
        MockSnapshot(observed_at=t0 + timedelta(seconds=30), outcome="player_a", odds=1.75),
        MockSnapshot(observed_at=t0 + timedelta(seconds=30), outcome="player_b", odds=2.05),
        MockSnapshot(observed_at=t0 + timedelta(seconds=60), outcome="player_a", odds=1.65),
        MockSnapshot(observed_at=t0 + timedelta(seconds=60), outcome="player_b", odds=2.20),
    ]

    vector = builder.build_features(
        event_id=ev_id,
        cutoff_time=cutoff,
        event_snapshots=states,
        odds_snapshots=odds,
    )

    assert isinstance(vector, FeatureVector)
    assert vector.event_id == ev_id
    assert vector.sport_code == "tennis"
    assert vector.feature_version == "1.0.0"
    assert vector.feature_cutoff_timestamp == cutoff

    f = vector.features

    # Check odds & no-vig
    assert f["current_odds_player_a"] == 1.65
    assert f["current_odds_player_b"] == 2.20
    assert f["no_vig_prob_player_a"] > f["no_vig_prob_player_b"]
    assert f["is_missing_odds"] == 0.0

    # Check rolling window features present
    for w in ROLLING_WINDOWS:
        assert f"odds_delta_player_a_{w}s" in f
        assert f"odds_velocity_player_a_{w}s" in f
        assert f"odds_volatility_player_a_{w}s" in f
        assert f"line_drift_player_a_{w}s" in f
        assert f"suspension_frequency_{w}s" in f

    # Check score differentials
    assert f["games_diff"] == 1.0  # 3 - 2 = 1.0
    assert f["points_diff"] == 1.0  # 40 (3.0) - 30 (2.0) = 1.0
    assert f["is_server_b"] == 1.0
    assert f["is_server_a"] == 0.0
    assert f["is_break_point"] == 1.0
    assert f["is_tiebreak"] == 0.0


def test_critical_zero_data_leakage():
    """
    CRITICAL ACCEPTANCE TEST:
    Verifies that observations after feature_cutoff_timestamp are STRICTLY ignored.
    Any changes in future snapshots must have ZERO influence on the features at cutoff.
    """
    builder = TennisFeatureBuilder()
    ev_id = str(uuid.uuid4())
    t0 = datetime(2026, 10, 8, 15, 0, 0, tzinfo=timezone.utc)
    cutoff = t0 + timedelta(seconds=30)

    # Valid snapshots before cutoff
    valid_states = [
        MockSnapshot(
            observed_at=t0,
            current_period=1,
            status="live",
            score="1-0",
            score_detail={"current_game_a": 1, "current_game_b": 0, "points_a": "0", "points_b": "0"},
            sport_state={"server": "player_a"},
        ),
        MockSnapshot(
            observed_at=t0 + timedelta(seconds=25),
            current_period=1,
            status="live",
            score="2-0",
            score_detail={"current_game_a": 2, "current_game_b": 0, "points_a": "15", "points_b": "0"},
            sport_state={"server": "player_a"},
        ),
    ]

    valid_odds = [
        MockSnapshot(observed_at=t0, outcome="player_a", odds=1.80),
        MockSnapshot(observed_at=t0, outcome="player_b", odds=2.00),
        MockSnapshot(observed_at=t0 + timedelta(seconds=20), outcome="player_a", odds=1.70),
        MockSnapshot(observed_at=t0 + timedelta(seconds=20), outcome="player_b", odds=2.15),
    ]

    # Baseline features at cutoff
    vec_baseline = builder.build_features(
        event_id=ev_id,
        cutoff_time=cutoff,
        event_snapshots=valid_states,
        odds_snapshots=valid_odds,
    )

    # Inject FUTURE snapshots (observed after cutoff)
    future_states = list(valid_states) + [
        MockSnapshot(
            observed_at=t0 + timedelta(seconds=120),  # FUTURE!
            current_period=1,
            status="finished",
            score="6-0",
            score_detail={"current_game_a": 6, "current_game_b": 0},
            sport_state={},
        )
    ]

    future_odds = list(valid_odds) + [
        MockSnapshot(observed_at=t0 + timedelta(seconds=60), outcome="player_a", odds=1.05),  # FUTURE!
        MockSnapshot(observed_at=t0 + timedelta(seconds=60), outcome="player_b", odds=15.0),  # FUTURE!
    ]

    vec_with_future = builder.build_features(
        event_id=ev_id,
        cutoff_time=cutoff,
        event_snapshots=future_states,
        odds_snapshots=future_odds,
    )

    # ZERO LEAKAGE ASSERTIONS:
    # 1. Current odds must NOT reflect future 1.05
    assert vec_with_future.features["current_odds_player_a"] == 1.70
    assert vec_with_future.features["current_odds_player_a"] == vec_baseline.features["current_odds_player_a"]

    # 2. Score must NOT reflect future 6-0
    assert vec_with_future.features["games_diff"] == 2.0
    assert vec_with_future.features["games_diff"] == vec_baseline.features["games_diff"]

    # 3. Exactly identical vectors across all numerical features!
    assert vec_with_future.features == vec_baseline.features


def test_critical_reproducibility_and_isolation():
    """
    CRITICAL ACCEPTANCE TEST:
    1. Reproducibility: Building features twice on identical data produces identical results.
    2. Isolation: Multiple events do not interfere with each other.
    """
    builder = TennisFeatureBuilder()
    ev1 = str(uuid.uuid4())
    ev2 = str(uuid.uuid4())
    t0 = datetime(2026, 10, 8, 16, 0, 0, tzinfo=timezone.utc)
    cutoff = t0 + timedelta(seconds=30)

    states_ev1 = [MockSnapshot(observed_at=t0, current_period=1, status="live", score="1-0", score_detail={"current_game_a": 1, "current_game_b": 0}, sport_state={"server": "player_a"})]
    odds_ev1 = [MockSnapshot(observed_at=t0, outcome="player_a", odds=1.50), MockSnapshot(observed_at=t0, outcome="player_b", odds=2.50)]

    # Run twice
    v1_run1 = builder.build_features(ev1, cutoff, states_ev1, odds_ev1)
    v1_run2 = builder.build_features(ev1, cutoff, states_ev1, odds_ev1)

    assert v1_run1.features == v1_run2.features

    # Event 2 has different data
    states_ev2 = [MockSnapshot(observed_at=t0, current_period=2, status="live", score="4-5", score_detail={"current_game_a": 4, "current_game_b": 5}, sport_state={"server": "player_b"})]
    odds_ev2 = [MockSnapshot(observed_at=t0, outcome="player_a", odds=3.20), MockSnapshot(observed_at=t0, outcome="player_b", odds=1.35)]

    v2 = builder.build_features(ev2, cutoff, states_ev2, odds_ev2)

    assert v1_run1.event_id != v2.event_id
    assert v1_run1.features["current_odds_player_a"] != v2.features["current_odds_player_a"]
    assert v1_run1.features["games_diff"] != v2.features["games_diff"]


if __name__ == "__main__":
    test_base_math_calculations()
    print("test_base_math_calculations: OK")
    test_tennis_feature_builder_context_and_windows()
    print("test_tennis_feature_builder_context_and_windows: OK")
    test_critical_zero_data_leakage()
    print("test_critical_zero_data_leakage: OK")
    test_critical_reproducibility_and_isolation()
    print("test_critical_reproducibility_and_isolation: OK")
    print("ALL FEATURE ENGINEERING TESTS PASSED!")
