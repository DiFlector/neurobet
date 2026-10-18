"""Epoch stopping and hyperparameter changes for tennis_v3.0.0."""

import io
import sys
from datetime import datetime, timedelta, timezone

import joblib
import numpy as np

for path in (
    "/srv/neurobet/services/neural",
    "/srv/neurobet/packages/contracts/src",
    "/srv/neurobet/packages/features/src",
    "/app",
):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.dataset import MatchSnapshots
from app.epoch_fit import (
    EpochTunedEstimator,
    _logistic_epoch_loop,
    neighbors,
    search_until_plateau,
    split_holdout,
)
from app.models import build_logistic_regression_pipeline


ORIGIN = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _match(index: int, label: int):
    started = ORIGIN + timedelta(days=index)
    rows = [[float(label), 0.2 if label else -0.2, 0.0] for _ in range(4)]
    return MatchSnapshots(f"m{index}", started, label, rows, [started] * 4, [label] * 4, ["match_winner"] * 4)


def test_holdout_is_the_latest_hundred_and_stays_out_of_training():
    matches = [_match(index, index % 2) for index in range(130)]
    trainable, holdout = split_holdout(matches, 100)
    assert len(holdout) == 100
    assert len(trainable) == 30
    assert set(match.event_id for match in trainable).isdisjoint(match.event_id for match in holdout)
    assert holdout[0].started_at > trainable[-1].started_at


def test_logistic_epochs_stop_when_validation_stops_improving():
    train = [_match(index, index % 2) for index in range(12)]
    val = [_match(index, index % 2) for index in range(12, 16)]
    report = _logistic_epoch_loop(train, val, 1.0, max_epochs=12, patience=2, min_delta=0.0001, seed=42)
    assert report["reason"] == "validation_plateau"
    assert report["best_epoch"] < 12
    assert len(report["curve"]) == report["best_epoch"] + 2
    assert report["curve"][report["best_epoch"] - 1]["log_loss"] <= min(row["log_loss"] for row in report["curve"]) + 0.0001


def test_search_follows_improvements_past_three_steps_and_then_stops():
    def evaluate(params, state):
        return {
            "params": dict(params),
            "log_loss": float(params["C"]),
            "brier": 0.2,
            "best_epoch": 1,
            "reason": "validation_plateau",
            "state": state,
        }

    found = search_until_plateau("lr", evaluate, {"C": 1.0})
    assert found["params"]["C"] == 0.0001
    assert len(found["visited"]) > 3
    assert neighbors("lr", {"C": 0.0001}) == [{"C": 0.001}]


def test_epoch_model_keeps_calibration_matches_out_of_the_base_and_reloads():
    matches = [_match(index, index % 2) for index in range(20)]
    model = EpochTunedEstimator(
        estimator=build_logistic_regression_pipeline().estimator,
        method="sigmoid",
        family="lr",
        params={"C": 1.0},
    )
    model.fit_matches(matches, ["strength", "noise", "market_is_set"])
    assert model.epoch_report_["best_epoch"] >= 1
    assert set(model.base_event_ids_).isdisjoint(model.calibration_event_ids_)
    buffer = io.BytesIO()
    joblib.dump(model, buffer)
    buffer.seek(0)
    loaded = joblib.load(buffer)
    row = np.asarray([[1.0, 0.2, 0.0]])
    assert np.allclose(loaded.predict_proba(row), model.predict_proba(row))


if __name__ == "__main__":
    test_holdout_is_the_latest_hundred_and_stays_out_of_training()
    test_logistic_epochs_stop_when_validation_stops_improving()
    test_search_follows_improvements_past_three_steps_and_then_stops()
    test_epoch_model_keeps_calibration_matches_out_of_the_base_and_reloads()
    print("test_epoch_fit: OK")
