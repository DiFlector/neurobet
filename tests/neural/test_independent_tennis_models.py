from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import torch

from app.independent_tennis_models import (
    ObservedFeatureState,
    TennisForecastGRU,
    build_causal_sequence,
    load_gru_checkpoint,
    fit_point_baseline,
    load_point_baseline_checkpoint,
    match_balanced_weights,
    masked_multitask_bce,
    save_gru_checkpoint,
    save_point_baseline_checkpoint,
    select_training_device,
)
from app.independent_tennis_models import (
    ODDS_FREE_SEQUENCE_FEATURE_NAMES,
    ODDS_FREE_STATIC_FEATURE_NAMES,
)


def test_causal_prefix_filters_future_collapses_only_adjacent_repeats_and_caps_at_64():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(70):
        value = float(33 if index in (66, 67) else index)
        rows.append(ObservedFeatureState(str(index), start + timedelta(seconds=index), (value, 1.0), index % 7 == 0))
    rows.append(ObservedFeatureState("future", start + timedelta(seconds=100), (999.0, 1.0)))

    sequence, length, ids = build_causal_sequence(
        rows, as_of=start + timedelta(seconds=69), feature_count=2
    )

    assert sequence.shape == (64, 4)
    assert length == 64
    assert "future" not in ids
    assert "66" in ids and "67" not in ids  # adjacent identical poll is collapsed
    assert ids[-1] == "69"
    assert np.isfinite(sequence).all()


def test_causal_prefix_preserves_non_adjacent_return_to_same_state():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        ObservedFeatureState("a1", start, (0.0,)),
        ObservedFeatureState("b", start + timedelta(seconds=1), (1.0,)),
        ObservedFeatureState("a2", start + timedelta(seconds=2), (0.0,)),
    ]
    _sequence, length, ids = build_causal_sequence(rows, as_of=start + timedelta(seconds=3), feature_count=1)
    assert length == 3
    assert ids == ("a1", "b", "a2")


def test_match_balancing_gives_each_match_equal_total_weight():
    weights = match_balanced_weights(["long", "long", "long", "short"])
    assert np.allclose(weights, [1 / 3, 1 / 3, 1 / 3, 1])
    assert weights[:3].sum() == pytest.approx(weights[3])


def test_masked_loss_ignores_unknown_target_labels():
    logits = torch.zeros((2, 4), requires_grad=True)
    labels = torch.tensor([[1.0, float("nan"), 0.0, float("nan")], [0.0, 1.0, float("nan"), float("nan")]])
    total, losses = masked_multitask_bce(logits, labels, torch.ones(2))
    assert total.item() == pytest.approx(float(np.log(2)))
    assert losses["point"] is not None and losses["game"] is not None
    assert losses["set"] is not None and losses["match"] is None
    total.backward()
    assert torch.isfinite(logits.grad).all()


def test_gru_checkpoint_save_load_parity_on_cpu(tmp_path):
    torch.manual_seed(17)
    static_size = len(ODDS_FREE_STATIC_FEATURE_NAMES)
    state_size = len(ODDS_FREE_SEQUENCE_FEATURE_NAMES)
    model = TennisForecastGRU(static_size=static_size, state_size=state_size, hidden_size=8).eval()
    static = torch.zeros((2, static_size))
    sequences = torch.zeros((2, 3, state_size + 2))
    sequences[1, 1:, 0] = torch.tensor([0.2, 0.4])
    lengths = torch.tensor([1, 3])
    with torch.no_grad():
        expected = model(static, sequences, lengths)

    checkpoint = tmp_path / "fixture-gru.pt"
    save_gru_checkpoint(str(checkpoint), model, seed=17, metadata={"fixture_only": True})
    loaded, metadata = load_gru_checkpoint(str(checkpoint), map_location="cpu")
    loaded.eval()
    with torch.no_grad():
        actual = loaded(static, sequences, lengths)

    assert torch.allclose(expected, actual)
    assert metadata["seed"] == 17
    assert metadata["metadata"]["fixture_only"] is True
    device, _name = select_training_device()
    assert device.type in {"cpu", "cuda"}


@pytest.mark.parametrize("method", ["lr", "gb"])
def test_fixture_point_baseline_fits_and_checkpoint_roundtrips(tmp_path, method):
    from app.tennis_forecast import ODDS_FREE_FEATURE_NAMES

    rng = np.random.default_rng(29)
    features = rng.normal(size=(24, len(ODDS_FREE_FEATURE_NAMES)))
    labels = np.asarray([index % 2 for index in range(24)], dtype=float)
    labels[0] = np.nan  # unknown target is masked, not converted to class zero
    event_ids = [f"fixture-match-{index // 2}" for index in range(24)]

    model, metadata = fit_point_baseline(
        features, labels, event_ids, method=method, seed=29
    )
    expected = model.predict_proba(features[1:5])
    assert metadata["known_examples"] == 23
    assert metadata["unique_matches"] == 12

    checkpoint = tmp_path / f"fixture-{method}.joblib"
    save_point_baseline_checkpoint(str(checkpoint), model, metadata)
    loaded, loaded_metadata = load_point_baseline_checkpoint(str(checkpoint))
    actual = loaded.predict_proba(features[1:5])

    assert np.allclose(expected, actual)
    assert loaded_metadata == metadata
