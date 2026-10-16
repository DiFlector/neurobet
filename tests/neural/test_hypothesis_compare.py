"""Frozen 100-match holdout and offline hypothesis candidates."""

from datetime import datetime, timedelta, timezone

import numpy as np

from app.compare.candidates.bagging import TreeBaggingCandidate
from app.compare.candidates.ensemble import logit
from app.compare.candidates.networks import GRUCandidate, MLPCandidate, ResidualMLPCandidate, _prefix_samples, _thin
from app.compare.rows import CompareRow, feature_vector
from app.compare.splits import assert_disjoint, event_ids, split_fixed_test
from sklearn.preprocessing import StandardScaler


def _row(event_id, start, label=1, snapshots=1):
    rows = []
    for index in range(snapshots):
        moment = start + timedelta(minutes=index)
        row = CompareRow(
            event_id=event_id,
            observed_at=moment,
            started_at=start,
            finished_at=start + timedelta(hours=2),
            phase="live",
            label=label,
            odds_a=1.7 if label else 2.2,
            odds_b=2.2 if label else 1.7,
            market_prob_a=0.62 if label else 0.38,
            sets_a=index % 2,
            sets_b=0,
            games_a=index % 5,
            games_b=1,
            points_a=index % 4,
            elo_diff=80.0 if label else -80.0,
        )
        rows.append(row)
    return rows


def _series(count, snapshots=1):
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        rows.extend(_row(f"m{index:04d}", origin + timedelta(days=index), index % 2, snapshots))
    return rows


def test_fixed_holdout_keeps_the_latest_100():
    rows = _series(130)
    train, validation, test = split_fixed_test(rows, test_matches=100, embargo=timedelta(0))
    assert_disjoint(train, validation, test)
    assert len(set(event_ids(test))) == 100
    assert event_ids(test) == [f"m{index:04d}" for index in range(30, 130)]
    assert max(row.started_at for row in train) < min(row.started_at for row in validation)
    assert max(row.started_at for row in validation) < min(row.started_at for row in test)
    assert not (set(event_ids(train)) | set(event_ids(validation))) & set(event_ids(test))


def test_embargo_removes_a_match_crossing_the_cut():
    rows = _series(30)
    long_match = [row for row in rows if row.event_id == "m0024"]
    long_match[0].finished_at = long_match[0].started_at + timedelta(days=2)
    train, validation, test = split_fixed_test(rows, test_matches=5, embargo=timedelta(hours=36))
    assert event_ids(test) == [f"m{index:04d}" for index in range(25, 30)]
    assert "m0024" not in set(event_ids(train) + event_ids(validation) + event_ids(test))
    assert_disjoint(train, validation, test)


def test_bagging_records_validation_trials():
    rows = _series(40)
    model = TreeBaggingCandidate()
    model.select(rows[:24], rows[24:])
    assert model.model is not None
    assert {item["kind"] for item in model.trials} == {"rf", "et"}
    probs = model.predict(rows[24:])
    assert probs.shape == (16,)
    assert np.all(np.isfinite(probs))
    assert np.all((probs >= 0.0) & (probs <= 1.0))


def test_network_refuses_test_matches():
    rows = _series(12, snapshots=2)
    model = MLPCandidate(max_epochs=1)
    model.forbidden = {rows[0].event_id}
    try:
        model.select(rows[:8], rows[8:])
    except RuntimeError as exc:
        assert "теста" in str(exc)
    else:
        raise AssertionError("network tuning accepted a frozen test match")


def test_mlp_is_reproducible_and_bounded():
    rows = _series(24, snapshots=2)
    first = MLPCandidate(max_epochs=2)
    second = MLPCandidate(max_epochs=2)
    first.select(rows[:32], rows[32:])
    second.select(rows[:32], rows[32:])
    left = first.predict(rows[32:])
    right = second.predict(rows[32:])
    assert left.shape == (len(rows[32:]),)
    assert np.all((left > 0.0) & (left < 1.0))
    assert np.allclose(left, right)
    assert first.bundle["parameters"] > 0
    assert first.bundle["device_name"] in {"cpu", "cuda"}


def test_residual_mlp_shift_stays_inside_the_clip():
    rows = _series(24, snapshots=2)
    model = ResidualMLPCandidate(max_epochs=2)
    model.select(rows[:32], rows[32:])
    predicted = model.predict(rows[32:])
    market = np.asarray([row.market_prob_a for row in rows[32:]], dtype=float)
    shift = np.abs(logit(predicted) - logit(market))
    assert float(shift.max()) <= 0.35 + 1e-5


def test_gru_prefix_hides_later_snapshots():
    assert _thin(20)[0] == 0
    assert _thin(20)[-1] == 19
    assert len(_thin(20)) == 12
    rows = _series(8, snapshots=20)
    scaler = StandardScaler().fit(np.asarray([feature_vector(row) for row in rows], dtype=np.float32))
    _features, _labels, lengths, _weights, positions = _prefix_samples(rows[:20], scaler)
    by_position = {int(position): int(length) for position, length in zip(positions, lengths)}
    assert by_position[0] == 1
    assert by_position[19] == 12
    model = GRUCandidate(max_epochs=2)
    model.select(rows[:120], rows[120:])
    predicted = model.predict(rows[120:])
    assert predicted.shape == (len(rows[120:]),)
    assert np.all((predicted > 0.0) & (predicted < 1.0))


if __name__ == "__main__":
    test_fixed_holdout_keeps_the_latest_100()
    test_embargo_removes_a_match_crossing_the_cut()
    test_bagging_records_validation_trials()
    test_network_refuses_test_matches()
    test_mlp_is_reproducible_and_bounded()
    test_residual_mlp_shift_stays_inside_the_clip()
    test_gru_prefix_hides_later_snapshots()
    print("test_hypothesis_compare: OK")
