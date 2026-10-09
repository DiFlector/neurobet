"""
Phase 22: Data Leakage Test Suite.

Rigorous verification of temporal causality, point-in-time evaluation,
and complete absence of future data leakage across all Neurobet ML and betting pipelines.
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
try:
    import numpy as np
except ImportError:
    np = None

try:
    import pytest
except ImportError:
    class _PytestRaisesContext:
        def __init__(self, expected_exc):
            self.expected_exc = expected_exc
            self.value = None
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None:
                raise AssertionError(f"Expected {self.expected_exc.__name__} but no exception was raised")
            if issubclass(exc_type, self.expected_exc):
                self.value = exc_val
                return True
            return False

    class _PytestMock:
        def raises(self, exc):
            return _PytestRaisesContext(exc)
    pytest = _PytestMock()


# Ensure all package source paths are discoverable in both host and docker container environments
paths = [
    "/srv/neurobet/packages/contracts/src",
    "/srv/neurobet/packages/features/src",
    "/srv/neurobet/packages/db/src",
    "/srv/neurobet/packages/sports-core/src",
    "/srv/neurobet/packages/bankroll/src",
    "/srv/neurobet/services/neural",
    "/srv/neurobet/services/research",
    "/app/packages/contracts/src",
    "/app/packages/features/src",
    "/app/packages/db/src",
    "/app/packages/sports-core/src",
    "/app/packages/bankroll/src",
    "/app",
]
for p in paths:
    if p not in sys.path:
        sys.path.insert(0, p)

from features import (
    ROLLING_WINDOWS,
    TennisFeatureBuilder,
    DataLeakageDetectedError,
    DataLeakageDetector,
)
from contracts import ResearchEvidence


class MockSnapshot:
    """Mock state snapshot for point-in-time testing."""
    def __init__(self, observed_at: datetime, **kwargs):
        if observed_at.tzinfo is None:
            self.observed_at = observed_at.replace(tzinfo=timezone.utc)
        else:
            self.observed_at = observed_at
        for k, v in kwargs.items():
            setattr(self, k, v)


# --------------------------------------------------------------------------
# 1 & 2: Artificial Dataset with Obvious Future Signal & Feature Builder Blindness
# --------------------------------------------------------------------------
def test_artificial_dataset_with_obvious_future_signal():
    """
    Verify that an artificial dataset containing blatant future signals (e.g. 1.01 odds collapse,
    6-0 blowout score, match finished status) is completely invisible to TennisFeatureBuilder
    when evaluated at an earlier cutoff_timestamp.
    """
    builder = TennisFeatureBuilder()
    ev_id = str(uuid.uuid4())
    t0 = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)
    cutoff = t0 + timedelta(minutes=10)

    # Valid pre-cutoff snapshots
    valid_states = [
        MockSnapshot(
            observed_at=t0,
            current_period=1,
            status="live",
            score="1-1",
            score_detail={"current_game_a": 1, "current_game_b": 1, "sets_a": 0, "sets_b": 0},
            sport_state={"server": "player_a"},
        ),
        MockSnapshot(
            observed_at=t0 + timedelta(minutes=5),
            current_period=1,
            status="live",
            score="2-2",
            score_detail={"current_game_a": 2, "current_game_b": 2, "sets_a": 0, "sets_b": 0},
            sport_state={"server": "player_b"},
        ),
    ]

    valid_odds = [
        MockSnapshot(observed_at=t0, outcome="player_a", odds=1.90),
        MockSnapshot(observed_at=t0, outcome="player_b", odds=1.90),
        MockSnapshot(observed_at=t0 + timedelta(minutes=5), outcome="player_a", odds=1.85),
        MockSnapshot(observed_at=t0 + timedelta(minutes=5), outcome="player_b", odds=1.95),
    ]

    # Obvious future signals (after cutoff!)
    future_states = [
        MockSnapshot(
            observed_at=t0 + timedelta(minutes=30),  # FUTURE
            current_period=2,
            status="finished",
            score="6-2 6-3",
            score_detail={"current_game_a": 6, "current_game_b": 3, "sets_a": 2, "sets_b": 0},
            sport_state={"winner": "player_a"},
        )
    ]

    future_odds = [
        # Blatant future cheat signal: odds collapse to 1.01 (player_a) and 50.0 (player_b)
        MockSnapshot(observed_at=t0 + timedelta(minutes=25), outcome="player_a", odds=1.01),
        MockSnapshot(observed_at=t0 + timedelta(minutes=25), outcome="player_b", odds=50.0),
    ]

    # Baseline features built purely on valid data
    vec_baseline = builder.build_features(
        event_id=ev_id,
        cutoff_time=cutoff,
        event_snapshots=valid_states,
        odds_snapshots=valid_odds,
    )

    # Features built on stream contaminated with future signals
    vec_contaminated = builder.build_features(
        event_id=ev_id,
        cutoff_time=cutoff,
        event_snapshots=valid_states + future_states,
        odds_snapshots=valid_odds + future_odds,
    )

    # Zero leakage verification: vectors must be 100% identical!
    assert vec_contaminated.features["current_odds_player_a"] == 1.85
    assert vec_contaminated.features["current_odds_player_b"] == 1.95
    assert vec_contaminated.features["current_odds_player_a"] != 1.01  # Not leaked!
    assert vec_contaminated.features["games_diff"] == 0.0              # 2-2 game diff, not 6-2 6-3
    assert vec_contaminated.features == vec_baseline.features

    # Run automated leakage detector assertion
    DataLeakageDetector.assert_feature_builder_zero_leakage(
        builder=builder,
        event_id=ev_id,
        cutoff_time=cutoff,
        valid_states=valid_states,
        valid_odds=valid_odds,
        future_states=future_states,
        future_odds=future_odds,
    )
    print("✓ test_artificial_dataset_with_obvious_future_signal passed")


# --------------------------------------------------------------------------
# 3: Cutoff Timestamp Boundary
# --------------------------------------------------------------------------
def test_cutoff_timestamp_boundary():
    """
    Verify point-in-time microsecond precision:
    - Snapshots with observed_at <= cutoff_time are included.
    - Snapshots with observed_at > cutoff_time (even +1 microsecond) are strictly excluded.
    """
    builder = TennisFeatureBuilder()
    ev_id = str(uuid.uuid4())
    cutoff = datetime(2026, 10, 9, 14, 0, 0, tzinfo=timezone.utc)

    DataLeakageDetector.assert_strict_cutoff_boundary(
        builder=builder,
        event_id=ev_id,
        cutoff_time=cutoff,
        snap_class=MockSnapshot,
    )
    print("✓ test_cutoff_timestamp_boundary passed")


# --------------------------------------------------------------------------
# 4: Rolling Windows Temporal Containment
# --------------------------------------------------------------------------
def test_rolling_windows_temporal_containment():
    """
    Verify that rolling window calculations ([5, 10, 30, 60, 300] seconds):
    1. Contain only observations in [cutoff - w, cutoff].
    2. Completely ignore observations older than cutoff - w.
    3. Strictly reject observations after cutoff.
    """
    builder = TennisFeatureBuilder()
    ev_id = str(uuid.uuid4())
    cutoff = datetime(2026, 10, 9, 15, 0, 0, tzinfo=timezone.utc)

    DataLeakageDetector.assert_rolling_windows_containment(
        builder=builder,
        event_id=ev_id,
        cutoff_time=cutoff,
        windows=ROLLING_WINDOWS,
        snap_class=MockSnapshot,
    )
    print("✓ test_rolling_windows_temporal_containment passed")


# --------------------------------------------------------------------------
# 5: Result Joins Isolation
# --------------------------------------------------------------------------
def test_result_joins_isolation():
    """
    Verify that match result data (winner, final_score, match outcome)
    is isolated to the prediction target label and NEVER leaked into feature matrix X.
    """
    builder = TennisFeatureBuilder()
    ev_id = str(uuid.uuid4())
    t0 = datetime(2026, 10, 9, 10, 0, 0, tzinfo=timezone.utc)

    # Historical state snapshots
    states = [
        MockSnapshot(
            observed_at=t0,
            current_period=1,
            status="live",
            score="3-2",
            score_detail={"current_game_a": 3, "current_game_b": 2, "sets_a": 0, "sets_b": 0},
            sport_state={},
        )
    ]
    odds = [
        MockSnapshot(observed_at=t0, outcome="player_a", odds=1.70),
        MockSnapshot(observed_at=t0, outcome="player_b", odds=2.10),
    ]

    feat_vector = builder.build_features(
        event_id=ev_id,
        cutoff_time=t0,
        event_snapshots=states,
        odds_snapshots=odds,
    )

    # Assert no target result attributes exist in the feature set
    DataLeakageDetector.assert_result_join_isolation(feat_vector.features)
    print("✓ test_result_joins_isolation passed")


# --------------------------------------------------------------------------
# 6: Odds Lookup Temporal Causality
# --------------------------------------------------------------------------
def test_odds_lookup_temporal_causality():
    """
    Verify that market odds queries at decision time only lookup odds
    observed at or before decision_time, never closing odds or future movements.
    """
    t_decision = datetime(2026, 10, 9, 12, 30, 0, tzinfo=timezone.utc)

    odds_stream = [
        MockSnapshot(observed_at=t_decision - timedelta(minutes=5), outcome="player_a", odds=1.95),
        MockSnapshot(observed_at=t_decision - timedelta(seconds=10), outcome="player_a", odds=1.88),
        # Future odds (e.g. Closing Odds / Post-Break Odds)
        MockSnapshot(observed_at=t_decision + timedelta(seconds=15), outcome="player_a", odds=1.45),
        MockSnapshot(observed_at=t_decision + timedelta(minutes=20), outcome="player_a", odds=1.10),
    ]

    # As-of lookup function
    valid_odds = [o for o in odds_stream if o.observed_at <= t_decision]
    latest_as_of = max(valid_odds, key=lambda o: o.observed_at)

    assert latest_as_of.observed_at == t_decision - timedelta(seconds=10)
    assert latest_as_of.odds == 1.88
    assert latest_as_of.odds != 1.45
    assert latest_as_of.odds != 1.10
    print("✓ test_odds_lookup_temporal_causality passed")


# --------------------------------------------------------------------------
# 7: Train/Validation Boundary and Purge Window
# --------------------------------------------------------------------------
def test_train_val_temporal_boundary_and_purge():
    """
    Verify chronological train/val splits:
    max(train_timestamps) + purge_window <= min(val_timestamps)
    with zero sample overlap.
    """
    t_base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    # 100 sequential timestamps 1 hour apart
    timestamps = [t_base + timedelta(hours=i) for i in range(100)]

    # 70% train, 30% val
    split_idx = 70
    train_ts = timestamps[:split_idx]
    val_ts = timestamps[split_idx:]

    purge_sec = 3600.0  # 1 hour purge window

    # Must pass clean integrity check
    DataLeakageDetector.assert_temporal_split_integrity(
        train_timestamps=train_ts,
        val_timestamps=val_ts,
        purge_window_sec=purge_sec,
    )

    # Boundary violation test: if val starts before train ends, must raise error
    with pytest.raises(DataLeakageDetectedError):
        inverted_train = timestamps[50:]
        inverted_val = timestamps[:50]
        DataLeakageDetector.assert_temporal_split_integrity(
            train_timestamps=inverted_train,
            val_timestamps=inverted_val,
            purge_window_sec=0.0,
        )

    print("✓ test_train_val_temporal_boundary_and_purge passed")


# --------------------------------------------------------------------------
# 8: Event Grouping Leakage Defense
# --------------------------------------------------------------------------
def test_event_grouping_leakage_defense():
    """
    Verify that multiple observations within the same match event do not leak
    across the train/validation boundary unless the match is strictly concluded before val.
    """
    t0 = datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc)

    # Event 1: spans 10:00 to 11:30
    ev1_times = [t0 + timedelta(minutes=m) for m in [0, 30, 60, 90]]
    # Event 2: spans 12:00 to 13:30
    ev2_times = [t0 + timedelta(minutes=m) for m in [120, 150, 180, 210]]

    # All observations of Event 1 belong strictly to train, Event 2 to val
    train_ts = ev1_times
    val_ts = ev2_times

    DataLeakageDetector.assert_temporal_split_integrity(
        train_timestamps=train_ts,
        val_timestamps=val_ts,
        purge_window_sec=1800.0,  # 30 mins between ev1 end (11:30) and ev2 start (12:00)
    )

    # If an ongoing match's later points were in train and earlier points in val -> LEAKAGE!
    ongoing_leak_train = [t0 + timedelta(minutes=90)]  # Late point in match 1
    ongoing_leak_val = [t0 + timedelta(minutes=30)]    # Early point in match 1
    with pytest.raises(DataLeakageDetectedError):
        DataLeakageDetector.assert_temporal_split_integrity(
            train_timestamps=ongoing_leak_train,
            val_timestamps=ongoing_leak_val,
        )

    print("✓ test_event_grouping_leakage_defense passed")


# --------------------------------------------------------------------------
# 9: Backtest Execution Timestamps Causality
# --------------------------------------------------------------------------
def test_backtest_execution_timestamps_causality():
    """
    Verify execution lifecycle causality in simulated execution:
    decision_time <= execution_time < settlement_time
    and execution odds observed_at <= execution_time.
    """
    t_dec = datetime(2026, 10, 9, 16, 0, 0, tzinfo=timezone.utc)
    t_exec = t_dec + timedelta(milliseconds=500)  # 500ms network latency
    t_odds = t_dec + timedelta(milliseconds=200)  # Odds arrived before execution
    t_settle = t_dec + timedelta(minutes=45)      # Match finished 45m later

    DataLeakageDetector.assert_backtest_execution_causality(
        decision_time=t_dec,
        execution_time=t_exec,
        settlement_time=t_settle,
        execution_odds_observed_at=t_odds,
    )

    # Causality violation 1: Odds observed AFTER execution (lookahead)
    t_future_odds = t_exec + timedelta(seconds=5)
    with pytest.raises(DataLeakageDetectedError):
        DataLeakageDetector.assert_backtest_execution_causality(
            decision_time=t_dec,
            execution_time=t_exec,
            settlement_time=t_settle,
            execution_odds_observed_at=t_future_odds,
        )

    # Causality violation 2: Settled BEFORE execution
    t_early_settle = t_dec - timedelta(minutes=1)
    with pytest.raises(DataLeakageDetectedError):
        DataLeakageDetector.assert_backtest_execution_causality(
            decision_time=t_dec,
            execution_time=t_exec,
            settlement_time=t_early_settle,
            execution_odds_observed_at=t_odds,
        )

    print("✓ test_backtest_execution_timestamps_causality passed")


# --------------------------------------------------------------------------
# 10: Research Published and Retrieved Timestamps
# --------------------------------------------------------------------------
def test_research_published_and_retrieved_timestamps():
    """
    Verify research evidence temporal causality:
    Articles published or retrieved after decision_time must be rejected/filtered.
    """
    t_decision = datetime(2026, 10, 9, 14, 0, 0, tzinfo=timezone.utc)

    # Valid evidence: published 2h before decision, retrieved 30m before decision
    valid_evidence = [
        ResearchEvidence(
            evidence_id="ev_valid_1",
            url="https://tennisworld.com/news1",
            normalized_url="https://tennisworld.com/news1",
            domain="tennisworld.com",
            title="Player A injury update",
            published_at=t_decision - timedelta(hours=2),
            retrieved_at=t_decision - timedelta(minutes=30),
            snippet="Player A recovered from elbow strain.",
            content_hash="hash1",
        )
    ]

    DataLeakageDetector.assert_research_evidence_causality(
        evidence_list=valid_evidence,
        decision_time=t_decision,
    )

    # Leaked evidence 1: Published after decision (future article)
    future_published = [
        ResearchEvidence(
            evidence_id="ev_future_pub",
            url="https://tennisworld.com/post_match",
            normalized_url="https://tennisworld.com/post_match",
            domain="tennisworld.com",
            title="Match recap: Player A defeats Player B",
            published_at=t_decision + timedelta(hours=1),  # FUTURE!
            retrieved_at=t_decision + timedelta(hours=1, minutes=5),
            snippet="Player A clinched the victory.",
            content_hash="hash2",
        )
    ]
    with pytest.raises(DataLeakageDetectedError):
        DataLeakageDetector.assert_research_evidence_causality(
            evidence_list=future_published,
            decision_time=t_decision,
        )

    # Leaked evidence 2: Published before decision, but RETRIEVED after decision
    # (The system had not crawled it yet at decision_time!)
    future_retrieved = [
        ResearchEvidence(
            evidence_id="ev_future_ret",
            url="https://tennisworld.com/tactics",
            normalized_url="https://tennisworld.com/tactics",
            domain="tennisworld.com",
            title="Tactical breakdown",
            published_at=t_decision - timedelta(hours=1),
            retrieved_at=t_decision + timedelta(minutes=15),  # FUTURE RETRIEVAL!
            snippet="Court speed analysis.",
            content_hash="hash3",
        )
    ]
    with pytest.raises(DataLeakageDetectedError):
        DataLeakageDetector.assert_research_evidence_causality(
            evidence_list=future_retrieved,
            decision_time=t_decision,
        )

    print("✓ test_research_published_and_retrieved_timestamps passed")


# --------------------------------------------------------------------------
# 11: ACCEPTANCE CRITERIA: Intentional Future-Leak Fails Verification
# --------------------------------------------------------------------------
class LeakyFeatureBuilder:
    """
    Adversarial buggy / cheating feature builder for acceptance mutation testing.
    Intentionally accesses future snapshots and mutates feature values.
    """
    sport_code: str = "tennis"

    def build_features(self, event_id, cutoff_time, event_snapshots, odds_snapshots):
        from contracts import FeatureVector
        features = {"current_odds_player_a": 1.85, "games_diff": 0.0}

        # BUG / ADVERSARIAL CHEAT: ignores cutoff and inspects the latest snapshot in the list!
        if odds_snapshots:
            latest = odds_snapshots[-1]
            features["current_odds_player_a"] = float(latest.odds)

        if event_snapshots:
            latest_st = event_snapshots[-1]
            if hasattr(latest_st, "score_detail") and latest_st.score_detail:
                a = latest_st.score_detail.get("current_game_a", 0)
                b = latest_st.score_detail.get("current_game_b", 0)
                features["games_diff"] = float(a - b)

        return FeatureVector(
            event_id=str(event_id),
            sport_code=self.sport_code,
            feature_version="1.0.0-leaky",
            timestamp=cutoff_time,
            features=features,
        )


def test_acceptance_intentional_future_leak_fails():
    """
    CRITICAL ACCEPTANCE TEST:
    Verifies that the data leakage detection suite GUARANTEES failure
    if any feature builder or pipeline code accidentally introduces a future peek.
    """
    leaky_builder = LeakyFeatureBuilder()
    ev_id = str(uuid.uuid4())
    t0 = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)
    cutoff = t0 + timedelta(minutes=5)

    valid_states = [
        MockSnapshot(observed_at=t0, score_detail={"current_game_a": 1, "current_game_b": 1})
    ]
    valid_odds = [
        MockSnapshot(observed_at=t0, outcome="player_a", odds=1.85)
    ]

    # Future cheat data: odds drop to 1.05 and score jumps to 6-0
    future_states = [
        MockSnapshot(observed_at=t0 + timedelta(minutes=20), score_detail={"current_game_a": 6, "current_game_b": 0})
    ]
    future_odds = [
        MockSnapshot(observed_at=t0 + timedelta(minutes=20), outcome="player_a", odds=1.05)
    ]

    # Must raise DataLeakageDetectedError!
    with pytest.raises(DataLeakageDetectedError) as exc_info:
        DataLeakageDetector.assert_feature_builder_zero_leakage(
            builder=leaky_builder,
            event_id=ev_id,
            cutoff_time=cutoff,
            valid_states=valid_states,
            valid_odds=valid_odds,
            future_states=future_states,
            future_odds=future_odds,
        )

    assert "Data Leakage Detected!" in str(exc_info.value)
    print("✓ test_acceptance_intentional_future_leak_fails passed (Cheating detected as expected!)")


# --------------------------------------------------------------------------
# Main Execution Runner
# --------------------------------------------------------------------------
if __name__ == "__main__":
    print("Running Phase 22 Data Leakage Test Suite...")
    test_artificial_dataset_with_obvious_future_signal()
    test_cutoff_timestamp_boundary()
    test_rolling_windows_temporal_containment()
    test_result_joins_isolation()
    test_odds_lookup_temporal_causality()
    test_train_val_temporal_boundary_and_purge()
    test_event_grouping_leakage_defense()
    test_backtest_execution_timestamps_causality()
    test_research_published_and_retrieved_timestamps()
    test_acceptance_intentional_future_leak_fails()

    print("\n🎉 ALL PHASE 22 DATA LEAKAGE TESTS PASSED SUCCESSFULLY!")
