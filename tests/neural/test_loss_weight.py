"""Weights and error slices for a loss copy. Existing lines keep gamma 0."""

from datetime import datetime, timezone

import numpy as np

from app.dataset import MatchSnapshots, example_weights, stack_matches
from app.metrics import (
    confidence_bucket,
    copy_beats_baseline,
    error_summary,
    loss_adjustment,
    sliced_metrics,
)
from app.models import build_logistic_regression_pipeline


def test_gamma_zero_keeps_match_weights_and_a_miss_weighs_more():
    base = np.asarray([0.5, 0.5], dtype=float)
    labels = np.asarray([1, 1], dtype=int)
    probabilities = np.asarray([0.05, 0.95], dtype=float)
    unchanged = example_weights(base, probabilities, labels, gamma=0.0)
    assert np.allclose(unchanged, base)
    weighted = example_weights(base, probabilities, labels, gamma=1.0)
    assert weighted[0] > weighted[1]

    now = datetime.now(timezone.utc)
    match = MatchSnapshots(
        event_id="m1",
        started_at=now,
        label=1,
        rows=[[0.1, 0.2], [0.2, 0.1]],
        observed_at=[now, now],
        row_labels=[1, 0],
    )
    _rows, _labels, weights = stack_matches([match])
    assert np.allclose(example_weights(weights, None, _labels, gamma=0.0), weights)


def test_slices_name_confidence_window_and_critical_misses():
    rows = [
        {
            "market": "match_winner",
            "odds": 1.35,
            "phase": "set_2",
            "label": 0,
            "probability": 0.9,
            "sport_code": "tennis",
        },
        {
            "market": "match_winner",
            "odds": 1.35,
            "phase": "set_2",
            "label": 1,
            "probability": 0.8,
            "sport_code": "tennis",
        },
    ]
    report = sliced_metrics(rows)
    assert "confidence:0.75+" in report
    assert "window:in_stake_window" in report
    summary = error_summary(rows)
    assert summary["decisions"] == 2
    assert summary["critical_misses"] == 1
    assert summary["confident_misses"] == 1
    assert confidence_bucket(0.55) == "0.50-0.60"
    assert confidence_bucket(0.7) == "0.60-0.75"


def test_adjustment_follows_the_gap_and_the_copy_must_miss_less():
    crowded = {"critical_misses": 4, "critical_misses_in_window": 3, "critical_misses_confident": 1}
    assert loss_adjustment(0.03, crowded) == "regularize"
    assert loss_adjustment(0.0, crowded) == "gamma"
    assert loss_adjustment(0.0, {"critical_misses": 0, "critical_misses_in_window": 0, "critical_misses_confident": 0}) == "none"
    assert copy_beats_baseline(
        {"critical_miss_rate": 0.4, "brier_score": 0.12},
        {"critical_miss_rate": 0.3, "brier_score": 0.13},
    )
    assert not copy_beats_baseline(
        {"critical_miss_rate": 0.4, "brier_score": 0.12},
        {"critical_miss_rate": 0.3, "brier_score": 0.15},
    )
    assert not copy_beats_baseline(
        {"critical_miss_rate": 0.4, "brier_score": 0.12},
        {"critical_miss_rate": 0.4, "brier_score": 0.12},
    )


def test_gamma_zero_fit_matches_the_ordinary_fit():
    now = datetime.now(timezone.utc)
    matches = []
    for index in range(12):
        label = index % 2
        rows = []
        for step in range(4):
            rows.append([float(label), float(step), 0.2 if label else -0.2])
        matches.append(
            MatchSnapshots(
                event_id=f"e{index}",
                started_at=now,
                label=label,
                rows=rows,
                observed_at=[now] * len(rows),
                row_labels=[label] * len(rows),
            )
        )
    plain = build_logistic_regression_pipeline(C=1.0, random_state=0, calibration_method="sigmoid")
    weighted = build_logistic_regression_pipeline(C=1.0, random_state=0, calibration_method="sigmoid")
    plain.fit_matches(matches)
    weighted.fit_matches(matches, loss_gamma=0.0)
    probe = np.asarray(matches[-1].rows, dtype=float)
    assert np.allclose(plain.predict_proba(probe), weighted.predict_proba(probe))


if __name__ == "__main__":
    test_gamma_zero_keeps_match_weights_and_a_miss_weighs_more()
    test_slices_name_confidence_window_and_critical_misses()
    test_adjustment_follows_the_gap_and_the_copy_must_miss_less()
    test_gamma_zero_fit_matches_the_ordinary_fit()
    print("test_loss_weight: OK")
