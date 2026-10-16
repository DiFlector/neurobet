"""Production-width holdout, prefix isolation, and residual bounds."""

from datetime import datetime, timedelta, timezone

import numpy as np

from app.compare.candidates.ensemble import logit
from app.compare.feature_models import (
    GRUFeatureModel,
    LogisticFeatureModel,
    ResidualMLPFeatureModel,
    group_mask,
    stack,
    _prefixes,
)
from app.compare.feature_split import event_ids, split_unseen_holdout
from app.dataset import MatchSnapshots
from sklearn.preprocessing import StandardScaler


def _names():
    names = [f"feature_{index:03d}" for index in range(103)]
    names[0] = "market_odds_a"
    names[1] = "market_odds_b"
    names[2] = "market_is_set"
    names[3] = "sets_diff"
    names[4] = "games_diff"
    names[5] = "player_prior_diff"
    return names


def _match(event_id, start, rows=4, label=1, market="match_winner"):
    names = _names()
    matrix = []
    moments = []
    markets = []
    labels = []
    for index in range(rows):
        row = [0.0] * 103
        row[0] = 1.7 if label else 2.2
        row[1] = 2.2 if label else 1.7
        row[2] = 1.0 if market.startswith("set") else 0.0
        row[3] = float(index if label else -index)
        matrix.append(row)
        moments.append(start + timedelta(minutes=index))
        markets.append(market)
        labels.append(label)
    return MatchSnapshots(event_id, start, label, matrix, moments, labels, markets)


def _series(count):
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [_match(f"m{index:04d}", origin + timedelta(days=index), label=index % 2) for index in range(count)]


def test_holdout_skips_the_previously_reported_block():
    matches = _series(250)
    reserved = [f"m{index:04d}" for index in range(150, 250)]
    train, validation, test = split_unseen_holdout(matches, reserved, test_matches=100)
    assert len(test) == 100
    assert event_ids(test) == [f"m{index:04d}" for index in range(50, 150)]
    used = set(event_ids(train) + event_ids(validation) + event_ids(test))
    assert used.isdisjoint(reserved)
    assert max(match.started_at for match in train) < min(match.started_at for match in validation)
    assert max(match.started_at for match in validation) < min(match.started_at for match in test)


def test_candidates_keep_the_production_width():
    names = _names()
    matches = _series(30)
    rows, _y, _w, _markets, _odds = stack(matches, names)
    assert rows.shape[1] == 103
    model = LogisticFeatureModel()
    model.select(matches[:20], matches[20:], names)
    assert model.model is not None
    assert {item["C"] for item in model.trials} == {0.01, 0.1, 1.0, 10.0}
    assert group_mask(names, "market").shape == (103,)
    assert group_mask(names, "score")[3]
    assert group_mask(names, "history")[5]


def test_gru_prefix_does_not_read_a_later_snapshot():
    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    match = _match("mixed", start, rows=4, market="match_winner")
    later = _match("mixed-set", start, rows=3, market="set_winner:1")
    match.rows.extend(later.rows)
    match.observed_at.extend(later.observed_at)
    match.row_labels.extend(later.row_labels)
    match.row_markets.extend(later.row_markets)
    names = _names()
    scaler = StandardScaler().fit(np.asarray(match.rows, dtype=float))
    _x, _y, lengths, _w, positions = _prefixes([match], names, scaler, with_positions=True)
    first_match = lengths[list(positions).index(0)]
    first_set = lengths[list(positions).index(4)]
    assert int(first_match) == 1
    assert int(first_set) == 1
    model = GRUFeatureModel(max_epochs=1)
    train = [_match(f"t{index}", start + timedelta(days=index + 1), label=index % 2) for index in range(12)]
    validation = [_match(f"v{index}", start + timedelta(days=20 + index), label=index % 2) for index in range(6)]
    model.select(train, validation, names)
    predicted = model.predict(validation, names)
    assert predicted.shape[0] == sum(len(item.rows) for item in validation)
    assert np.all((predicted > 0.0) & (predicted < 1.0))


def test_residual_shift_stays_bounded_on_the_wide_tape():
    names = _names()
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    train = [_match(f"t{index}", start + timedelta(days=index), rows=2, label=index % 2) for index in range(16)]
    validation = [_match(f"v{index}", start + timedelta(days=30 + index), rows=2, label=index % 2) for index in range(8)]
    model = ResidualMLPFeatureModel(max_epochs=1)
    model.select(train, validation, names)
    model.fit_final(train, validation, names)
    predicted = model.predict(validation, names)
    from app.compare.feature_models import market_probability

    market = market_probability(validation, names)
    shift = np.abs(logit(predicted) - logit(market))
    assert predicted.shape == market.shape
    assert float(shift.max()) <= 0.35 + 1e-5


if __name__ == "__main__":
    test_holdout_skips_the_previously_reported_block()
    test_candidates_keep_the_production_width()
    test_gru_prefix_does_not_read_a_later_snapshot()
    test_residual_shift_stays_bounded_on_the_wide_tape()
    print("test_feature103_compare: OK")
