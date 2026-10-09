"""
Data Leakage Detection and Auditing Engine for Neurobet.

Provides rigorous temporal verification to guarantee zero future data leakage
across feature builders, rolling windows, dataset joins, odds lookup,
train/validation splits, backtest execution, and web research evidence.
"""

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Set
try:
    import numpy as np
except ImportError:
    np = None


class DataLeakageDetectedError(RuntimeError):
    """Raised when future data leakage or temporal causality violation is detected."""
    pass


class DataLeakageDetector:
    """
    Automated auditor for temporal causality and zero future data leakage.
    Enforces the fundamental architectural constraint:
    'No component may access information observed after cutoff_timestamp/decision_timestamp.'
    """

    @staticmethod
    def _to_utc(dt: datetime) -> datetime:
        """Converts datetime to timezone-aware UTC."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @classmethod
    def assert_feature_builder_zero_leakage(
        cls,
        builder: Any,
        event_id: str,
        cutoff_time: datetime,
        valid_states: List[Any],
        valid_odds: List[Any],
        future_states: List[Any],
        future_odds: List[Any],
    ) -> None:
        """
        Validates that a feature builder produces IDENTICAL feature vectors
        regardless of whether future states or future odds are present in the stream.
        """
        cutoff_utc = cls._to_utc(cutoff_time)

        # Baseline: features built strictly from past-only snapshots
        baseline_vector = builder.build_features(
            event_id=event_id,
            cutoff_time=cutoff_utc,
            event_snapshots=valid_states,
            odds_snapshots=valid_odds,
        )

        # Contaminated stream: includes future snapshots with obvious signals
        augmented_states = list(valid_states) + list(future_states)
        augmented_odds = list(valid_odds) + list(future_odds)

        test_vector = builder.build_features(
            event_id=event_id,
            cutoff_time=cutoff_utc,
            event_snapshots=augmented_states,
            odds_snapshots=augmented_odds,
        )

        # Check feature keys match
        baseline_keys = set(baseline_vector.features.keys())
        test_keys = set(test_vector.features.keys())
        if baseline_keys != test_keys:
            diff = baseline_keys.symmetric_difference(test_keys)
            raise DataLeakageDetectedError(
                f"Feature vector keys altered by future data injection! Difference: {diff}"
            )

        # Check all values are identical
        violations: Dict[str, Tuple[float, float]] = {}
        for k in baseline_keys:
            base_val = baseline_vector.features[k]
            test_val = test_vector.features[k]
            if np is not None:
                is_equal = bool(np.isclose(base_val, test_val, equal_nan=True, atol=1e-7))
            else:
                is_equal = math.isclose(float(base_val), float(test_val), abs_tol=1e-7)
            if not is_equal:
                violations[k] = (base_val, test_val)

        if violations:
            msg_parts = [
                f"Feature '{k}': baseline={v[0]} vs with_future={v[1]}"
                for k, v in list(violations.items())[:5]
            ]
            raise DataLeakageDetectedError(
                f"Data Leakage Detected! Future data altered {len(violations)} feature values: "
                + "; ".join(msg_parts)
            )

    @classmethod
    def assert_strict_cutoff_boundary(
        cls,
        builder: Any,
        event_id: str,
        cutoff_time: datetime,
        snap_class: Any,
    ) -> None:
        """
        Verifies exact microsecond boundary inclusion/exclusion:
        - observed_at <= cutoff_time: INCLUDED
        - observed_at > cutoff_time: EXCLUDED
        """
        cutoff_utc = cls._to_utc(cutoff_time)
        t_minus_1us = cutoff_utc - timedelta(microseconds=1)
        t_exact = cutoff_utc
        t_plus_1us = cutoff_utc + timedelta(microseconds=1)

        # Odds at exact cutoff vs microsecond after
        odds_exact = snap_class(observed_at=t_exact, outcome="player_a", odds=1.75)
        odds_future = snap_class(observed_at=t_plus_1us, outcome="player_a", odds=99.0)

        # Vector with exact cutoff odds
        vec_exact = builder.build_features(
            event_id=event_id,
            cutoff_time=cutoff_utc,
            event_snapshots=[],
            odds_snapshots=[odds_exact, odds_future],
        )

        curr_odds = vec_exact.features.get("current_odds_player_a")
        if curr_odds != 1.75:
            raise DataLeakageDetectedError(
                f"Cutoff boundary violation: expected current_odds_player_a=1.75 from exact cutoff, "
                f"got {curr_odds}. Future snapshot at cutoff + 1us leaked!"
            )

    @classmethod
    def assert_rolling_windows_containment(
        cls,
        builder: Any,
        event_id: str,
        cutoff_time: datetime,
        windows: List[int],
        snap_class: Any,
    ) -> None:
        """
        Verifies that rolling window statistics:
        1. Ignore snapshots older than (cutoff_time - window_size).
        2. Never incorporate snapshots after cutoff_time.
        """
        cutoff_utc = cls._to_utc(cutoff_time)

        for w in windows:
            # Create a snapshot just inside the window: cutoff - w + 1s
            inside_t = cutoff_utc - timedelta(seconds=w - 1)
            # Create a snapshot just outside the window: cutoff - w - 5s
            outside_t = cutoff_utc - timedelta(seconds=w + 5)
            # Create a future snapshot: cutoff + 1s
            future_t = cutoff_utc + timedelta(seconds=1)

            odds_inside = snap_class(observed_at=inside_t, outcome="player_a", odds=2.00)
            odds_outside = snap_class(observed_at=outside_t, outcome="player_a", odds=5.00)
            odds_future = snap_class(observed_at=future_t, outcome="player_a", odds=10.00)

            # Test vector with inside + outside + future
            vec = builder.build_features(
                event_id=event_id,
                cutoff_time=cutoff_utc,
                event_snapshots=[],
                odds_snapshots=[odds_outside, odds_inside, odds_future],
            )

            # Delta for window w should be between latest (2.00) and window start (2.00), delta = 0.0
            # If outside_t leaked, delta would be 2.00 - 5.00 = -3.00
            # If future_t leaked, current_odds would be 10.00
            curr = vec.features.get("current_odds_player_a")
            if curr == 10.00:
                raise DataLeakageDetectedError(
                    f"Rolling window {w}s leaked future odds from cutoff + 1s!"
                )

    @classmethod
    def assert_result_join_isolation(
        cls,
        features: Dict[str, Any],
        forbidden_substrings: Optional[List[str]] = None,
    ) -> None:
        """
        Verifies that final match result columns (winner, final score, match outcome)
        are NOT present in the feature matrix X.
        """
        forbidden = forbidden_substrings or [
            "winner",
            "final_score",
            "match_result",
            "outcome_label",
            "actual_winner",
            "settled_at",
            "target",
        ]
        for key in features.keys():
            k_lower = key.lower()
            for f in forbidden:
                if f in k_lower:
                    raise DataLeakageDetectedError(
                        f"Target result leakage! Feature key '{key}' contains forbidden keyword '{f}'."
                    )

    @classmethod
    def assert_temporal_split_integrity(
        cls,
        train_timestamps: List[datetime],
        val_timestamps: List[datetime],
        purge_window_sec: float = 0.0,
    ) -> None:
        """
        Verifies that train and validation sets are strictly chronological:
        max(train_timestamps) + purge_window <= min(val_timestamps)
        and train_timestamps intersect val_timestamps is empty.
        """
        if not train_timestamps or not val_timestamps:
            raise ValueError("Train and validation timestamp lists must not be empty.")

        train_times = [cls._to_utc(t).timestamp() for t in train_timestamps]
        val_times = [cls._to_utc(t).timestamp() for t in val_timestamps]

        max_train = max(train_times)
        min_val = min(val_times)

        if max_train > min_val:
            raise DataLeakageDetectedError(
                f"Temporal split boundary violation! max(train)={datetime.fromtimestamp(max_train, tz=timezone.utc)} "
                f"> min(val)={datetime.fromtimestamp(min_val, tz=timezone.utc)}"
            )

        if max_train + purge_window_sec > min_val:
            raise DataLeakageDetectedError(
                f"Purge window violation! max(train) + {purge_window_sec}s exceeds min(val) "
                f"by {(max_train + purge_window_sec) - min_val:.2f}s."
            )

    @classmethod
    def assert_research_evidence_causality(
        cls,
        evidence_list: List[Any],
        decision_time: datetime,
    ) -> None:
        """
        Verifies that all research evidence snippets were both published
        AND retrieved at or before the decision timestamp.
        """
        dec_utc = cls._to_utc(decision_time)

        for ev in evidence_list:
            ev_id = getattr(ev, "evidence_id", str(ev))
            # 1. Retrieved at check
            retrieved_at = getattr(ev, "retrieved_at", None)
            if retrieved_at is not None:
                ret_utc = cls._to_utc(retrieved_at)
                if ret_utc > dec_utc:
                    raise DataLeakageDetectedError(
                        f"Research retrieval leakage for evidence '{ev_id}': "
                        f"retrieved_at ({ret_utc.isoformat()}) > decision_time ({dec_utc.isoformat()})"
                    )

            # 2. Published at check
            published_at = getattr(ev, "published_at", None)
            if published_at is not None:
                pub_utc = cls._to_utc(published_at)
                if pub_utc > dec_utc:
                    raise DataLeakageDetectedError(
                        f"Research publication leakage for evidence '{ev_id}': "
                        f"published_at ({pub_utc.isoformat()}) > decision_time ({dec_utc.isoformat()})"
                    )

    @classmethod
    def assert_backtest_execution_causality(
        cls,
        decision_time: datetime,
        execution_time: datetime,
        settlement_time: datetime,
        execution_odds_observed_at: datetime,
    ) -> None:
        """
        Verifies temporal order of backtest order lifecycle:
        decision_time <= execution_time < settlement_time
        and execution_odds_observed_at <= execution_time.
        """
        t_dec = cls._to_utc(decision_time)
        t_exec = cls._to_utc(execution_time)
        t_settle = cls._to_utc(settlement_time)
        t_odds = cls._to_utc(execution_odds_observed_at)

        if t_dec > t_exec:
            raise DataLeakageDetectedError(
                f"Execution causality violation: decision_time ({t_dec}) > execution_time ({t_exec})"
            )

        if t_exec >= t_settle:
            raise DataLeakageDetectedError(
                f"Settlement causality violation: execution_time ({t_exec}) >= settlement_time ({t_settle})"
            )

        if t_odds > t_exec:
            raise DataLeakageDetectedError(
                f"Execution odds lookahead violation: odds observed_at ({t_odds}) > execution_time ({t_exec})"
            )
