"""Market-specific temporal calibration for tennis match and set rows."""

from datetime import datetime, timedelta, timezone

import io

import joblib
import numpy as np

from app.dataset import MatchSnapshots
from app.metrics import family_metrics
from app.models import TemporalCalibratedEstimator, build_logistic_regression_pipeline

NAMES = ["strength", "market_is_set"]
ORIGIN = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _match(index: int, match_label: int, set_label: int, match_rows: int = 4, set_rows: int = 4):
    started = ORIGIN + timedelta(days=index)
    rows = []
    labels = []
    markets = []
    for _ in range(match_rows):
        rows.append([float(match_label), 0.0])
        labels.append(match_label)
        markets.append("match_winner")
    for _ in range(set_rows):
        rows.append([float(set_label), 1.0])
        labels.append(set_label)
        markets.append("set_winner:1")
    return MatchSnapshots(
        event_id=f"m{index}",
        started_at=started,
        label=match_label,
        rows=rows,
        observed_at=[started] * len(rows),
        row_labels=labels,
        row_markets=markets,
    )


def _series(count: int = 20, set_rows: int = 4):
    matches = []
    for index in range(count):
        match_label = index % 2
        set_label = 1 if index % 3 == 0 else 0
        matches.append(_match(index, match_label, set_label, set_rows=set_rows))
    return matches


def test_calibrators_see_only_their_market_and_later_matches():
    model = build_logistic_regression_pipeline(calibration_method="isotonic")
    model.fit_matches(_series(), NAMES)
    report = model.calibration_report_
    assert report["match"]["method"] == "sigmoid"
    assert report["match"]["reason"] == "sigmoid_until_1000_rows"
    assert report["set"]["method"] == "sigmoid"
    assert report["match"]["rows"] > 0
    assert report["set"]["rows"] > 0
    assert report["match"]["label_mean"] != report["set"]["label_mean"]
    assert set(model.base_event_ids_).isdisjoint(model.calibration_event_ids_)
    assert int(model.base_event_ids_[-1][1:]) < int(model.calibration_event_ids_[0][1:])


def test_short_set_holdout_falls_back_without_fitting_set_calibrator():
    short = _series(count=12, set_rows=1)
    model = build_logistic_regression_pipeline(calibration_method="sigmoid")
    model.fit_matches(short, NAMES)
    assert model.calibration_report_["set"]["reason"] in ("insufficient_rows", "fallback_to_match")
    assert model.calibration_report_["set"].get("fallback") == "match" or model.model_.set_calibrator is None
    row = np.asarray([[1.0, 1.0]])
    probs = model.predict_proba(row)
    assert probs.shape == (1, 2)


def test_inference_uses_the_row_market():
    model = build_logistic_regression_pipeline(calibration_method="sigmoid")
    model.fit_matches(_series(count=30), NAMES)
    match_row = np.asarray([[1.0, 0.0]])
    set_row = np.asarray([[1.0, 1.0]])
    aware = model.model_
    assert np.allclose(model.predict_proba(match_row), aware.match_calibrator.predict_proba(match_row))
    assert np.allclose(model.predict_proba(set_row), aware.set_calibrator.predict_proba(set_row))


def test_checkpoint_roundtrip_keeps_market_choice():
    model = build_logistic_regression_pipeline(calibration_method="sigmoid")
    model.fit_matches(_series(), NAMES)
    buffer = io.BytesIO()
    joblib.dump({"model": model, "feature_names": NAMES}, buffer)
    buffer.seek(0)
    loaded = joblib.load(buffer)["model"]
    row = np.asarray([[0.0, 1.0], [0.0, 0.0]])
    assert np.allclose(loaded.predict_proba(row), model.predict_proba(row))
    assert isinstance(loaded, TemporalCalibratedEstimator)


def test_future_match_is_outside_the_base_fit():
    matches = _series()
    model = build_logistic_regression_pipeline(calibration_method="sigmoid")
    model.fit_matches(matches, NAMES)
    future = matches[-1].event_id
    assert future in model.calibration_event_ids_
    assert future not in model.base_event_ids_


def test_family_metrics_keep_match_and_set_apart():
    records = [
        {"market": "match_winner", "market_family": "match", "label": 1, "probability": 0.7},
        {"market": "match_winner", "market_family": "match", "label": 0, "probability": 0.4},
        {"market": "set_winner:1", "market_family": "set", "label": 1, "probability": 0.8},
        {"market": "set_winner:2", "market_family": "set", "label": 0, "probability": 0.2},
    ]
    report = family_metrics(records)
    assert report["match"]["count"] == 2
    assert report["set"]["count"] == 2
    assert "brier_score" in report["match"]
    assert report["set"]["reliability"]


if __name__ == "__main__":
    test_calibrators_see_only_their_market_and_later_matches()
    test_short_set_holdout_falls_back_without_fitting_set_calibrator()
    test_inference_uses_the_row_market()
    test_checkpoint_roundtrip_keeps_market_choice()
    test_future_match_is_outside_the_base_fit()
    test_family_metrics_keep_match_and_set_apart()
    print("OK")
