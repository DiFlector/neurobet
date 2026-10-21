"""Research-only, odds-free model primitives for independent tennis forecasts.

This module deliberately contains no database access or training-data loader.
Callers must provide source-certified feature rows and labels; unknown targets
are represented by NaN and masked from the loss.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import random
from typing import Iterable, Sequence

import numpy as np
import torch
from torch import nn

from .tennis_forecast import (
    ODDS_FREE_FEATURE_NAMES,
    ODDS_FREE_FEATURE_SOURCE_GROUPS,
    ODDS_FREE_FEATURE_VERSION,
)

TARGET_NAMES = ("point", "game", "set", "match")
MAX_SEQUENCE_STATES = 64
CHECKPOINT_SCHEMA_VERSION = 1
_STATIC_GROUPS = ("player_metrics", "serve_metrics_a", "serve_metrics_b", "event_surface")
ODDS_FREE_STATIC_FEATURE_NAMES = tuple(
    name for group in _STATIC_GROUPS for name in ODDS_FREE_FEATURE_SOURCE_GROUPS[group]
)
ODDS_FREE_SEQUENCE_FEATURE_NAMES = ODDS_FREE_FEATURE_SOURCE_GROUPS["event_state"]


@dataclass(frozen=True)
class ObservedFeatureState:
    state_id: str
    observed_at: datetime
    features: tuple[float, ...]
    has_missing_fields: bool = False


def seed_everything(seed: int) -> None:
    """Set reproducible CPU/GPU seeds for fixture and later development runs."""
    seed = int(seed)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def select_training_device() -> tuple[torch.device, str]:
    """Prefer supported CUDA, otherwise use the deterministic CPU path."""
    if torch.cuda.is_available():
        major, minor = torch.cuda.get_device_capability()
        if major >= 7:
            return torch.device("cuda"), torch.cuda.get_device_name()
        return torch.device("cpu"), f"cpu_fallback:unsupported_cuda_sm_{major}{minor}"
    return torch.device("cpu"), "cpu"


def match_balanced_weights(event_ids: Sequence[str]) -> np.ndarray:
    """Give each match unit total weight, irrespective of its snapshot count."""
    if not event_ids:
        return np.zeros(0, dtype=np.float32)
    counts: dict[str, int] = {}
    for event_id in event_ids:
        key = str(event_id)
        counts[key] = counts.get(key, 0) + 1
    weights = np.asarray([1.0 / counts[str(event_id)] for event_id in event_ids], dtype=np.float32)
    return weights


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def build_causal_sequence(
    states: Iterable[ObservedFeatureState],
    *,
    as_of: datetime,
    feature_count: int = len(ODDS_FREE_SEQUENCE_FEATURE_NAMES),
    max_states: int = MAX_SEQUENCE_STATES,
) -> tuple[np.ndarray, int, tuple[str, ...]]:
    """Build a last-N prefix using only source observations available by cutoff.

    Consecutive identical polls are collapsed. A score/state that returns after
    an intervening state remains a distinct row (A -> B -> A is preserved).
    Each sequence row appends log elapsed seconds and a missing-field flag.
    """
    if max_states < 1 or max_states > MAX_SEQUENCE_STATES:
        raise ValueError("max_states must be in [1, 64]")
    cutoff = _utc(as_of)
    ordered = sorted(
        (row for row in states if _utc(row.observed_at) <= cutoff),
        key=lambda row: _utc(row.observed_at),
    )
    distinct: list[ObservedFeatureState] = []
    last_signature = None
    for row in ordered:
        vector = np.asarray(row.features, dtype=np.float32)
        if vector.shape != (feature_count,) or not np.isfinite(vector).all():
            raise ValueError("invalid_observed_feature_vector")
        signature = (vector.tobytes(), bool(row.has_missing_fields))
        if signature == last_signature:
            continue
        distinct.append(row)
        last_signature = signature
    if not distinct:
        return np.zeros((0, feature_count + 2), dtype=np.float32), 0, ()

    enriched = []
    previous_at = None
    for row in distinct:
        observed_at = _utc(row.observed_at)
        seconds = 0.0 if previous_at is None else max(0.0, (observed_at - previous_at).total_seconds())
        enriched.append(np.concatenate((
            np.asarray(row.features, dtype=np.float32),
            np.asarray([math.log1p(seconds), float(bool(row.has_missing_fields))], dtype=np.float32),
        )))
        previous_at = observed_at

    selected_rows = enriched[-max_states:]
    selected_states = distinct[-max_states:]
    return np.stack(selected_rows), len(selected_rows), tuple(row.state_id for row in selected_states)


class TennisForecastGRU(nn.Module):
    """Causal GRU with static player features and four masked target heads."""

    def __init__(self, *, static_size: int = len(ODDS_FREE_STATIC_FEATURE_NAMES),
                 state_size: int = len(ODDS_FREE_SEQUENCE_FEATURE_NAMES), hidden_size: int = 64):
        super().__init__()
        if static_size < 1 or state_size < 1 or hidden_size < 1:
            raise ValueError("model dimensions must be positive")
        self.static_size = int(static_size)
        self.state_size = int(state_size)
        self.hidden_size = int(hidden_size)
        self.sequence_size = self.state_size + 2
        self.gru = nn.GRU(self.sequence_size, self.hidden_size, batch_first=True)
        self.heads = nn.ModuleDict({name: nn.Linear(self.hidden_size + self.static_size, 1) for name in TARGET_NAMES})

    def forward(self, static_features: torch.Tensor, sequences: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
        if static_features.ndim != 2 or static_features.shape[1] != self.static_size:
            raise ValueError("static_features_shape_mismatch")
        if sequences.ndim != 3 or sequences.shape[2] != self.sequence_size:
            raise ValueError("sequence_shape_mismatch")
        if sequences.shape[1] > MAX_SEQUENCE_STATES:
            raise ValueError("sequence_exceeds_64_states")
        if lengths.ndim != 1 or len(lengths) != len(static_features):
            raise ValueError("sequence_lengths_shape_mismatch")
        if torch.any(lengths <= 0) or torch.any(lengths > sequences.shape[1]):
            raise ValueError("invalid_sequence_lengths")
        packed = nn.utils.rnn.pack_padded_sequence(
            sequences, lengths.detach().cpu(), batch_first=True, enforce_sorted=False
        )
        _output, hidden = self.gru(packed)
        joined = torch.cat((static_features, hidden[-1]), dim=1)
        return torch.cat([self.heads[name](joined) for name in TARGET_NAMES], dim=1)


def masked_multitask_bce(
    logits: torch.Tensor,
    labels: torch.Tensor,
    sample_weights: torch.Tensor | None = None,
) -> tuple[torch.Tensor, dict[str, torch.Tensor | None]]:
    """Average weighted BCE only over known target labels; NaNs are masked."""
    if logits.shape != labels.shape or logits.ndim != 2 or logits.shape[1] != len(TARGET_NAMES):
        raise ValueError("multitask_tensor_shape_mismatch")
    if sample_weights is None:
        sample_weights = torch.ones(logits.shape[0], dtype=logits.dtype, device=logits.device)
    if sample_weights.shape != (logits.shape[0],):
        raise ValueError("sample_weights_shape_mismatch")
    if torch.any(sample_weights < 0):
        raise ValueError("negative_sample_weight")

    losses: dict[str, torch.Tensor | None] = {}
    available = []
    for index, name in enumerate(TARGET_NAMES):
        mask = torch.isfinite(labels[:, index])
        if not torch.any(mask):
            losses[name] = None
            continue
        weights = sample_weights[mask]
        target_losses = nn.functional.binary_cross_entropy_with_logits(
            logits[mask, index], labels[mask, index], reduction="none"
        )
        loss = (target_losses * weights).sum() / weights.sum().clamp_min(1e-12)
        losses[name] = loss
        available.append(loss)
    if not available:
        raise ValueError("batch_has_no_known_targets")
    return torch.stack(available).mean(), losses


def save_gru_checkpoint(path: str, model: TennisForecastGRU, *, seed: int, metadata: dict | None = None) -> None:
    payload = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "model_type": "independent_tennis_gru",
        "feature_version": ODDS_FREE_FEATURE_VERSION,
        "feature_names": list(ODDS_FREE_FEATURE_NAMES),
        "static_feature_names": list(ODDS_FREE_STATIC_FEATURE_NAMES),
        "sequence_feature_names": list(ODDS_FREE_SEQUENCE_FEATURE_NAMES),
        "targets": list(TARGET_NAMES),
        "seed": int(seed),
        "architecture": {"static_size": model.static_size, "hidden_size": model.hidden_size},
        "metadata": metadata or {},
        "state_dict": model.state_dict(),
    }
    torch.save(payload, path)


def load_gru_checkpoint(path: str, *, map_location: str | torch.device = "cpu") -> tuple[TennisForecastGRU, dict]:
    payload = torch.load(path, map_location=map_location, weights_only=False)
    if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("unsupported_checkpoint_schema")
    if payload.get("feature_version") != ODDS_FREE_FEATURE_VERSION:
        raise ValueError("checkpoint_feature_version_mismatch")
    if payload.get("feature_names") != list(ODDS_FREE_FEATURE_NAMES):
        raise ValueError("checkpoint_feature_names_mismatch")
    if payload.get("static_feature_names") != list(ODDS_FREE_STATIC_FEATURE_NAMES):
        raise ValueError("checkpoint_static_feature_names_mismatch")
    if payload.get("sequence_feature_names") != list(ODDS_FREE_SEQUENCE_FEATURE_NAMES):
        raise ValueError("checkpoint_sequence_feature_names_mismatch")
    if payload.get("targets") != list(TARGET_NAMES):
        raise ValueError("checkpoint_target_names_mismatch")
    model = TennisForecastGRU(**payload["architecture"])
    model.load_state_dict(payload["state_dict"])
    model.to(map_location)
    return model, {key: value for key, value in payload.items() if key != "state_dict"}


def fit_point_baseline(
    features: np.ndarray,
    labels: Sequence[float],
    event_ids: Sequence[str],
    *,
    method: str,
    seed: int,
) -> tuple[object, dict]:
    """Fit a point-probability LR or GB candidate on an explicitly supplied matrix.

    There is intentionally no DB access, automatic data loading, gate bypass,
    or model registry write here. Callers own the admission gate and PIT proof.
    This primitive is used only by deterministic synthetic-fixture tests until
    a certified training manifest is admitted.
    """
    x = np.asarray(features, dtype=np.float64)
    y = np.asarray(labels, dtype=np.float64)
    if x.ndim != 2 or x.shape[1] != len(ODDS_FREE_FEATURE_NAMES):
        raise ValueError("odds_free_feature_contract_shape_mismatch")
    if y.shape != (len(x),) or len(event_ids) != len(x):
        raise ValueError("point_training_rows_shape_mismatch")
    if not np.isfinite(x).all():
        raise ValueError("non_finite_point_features")
    known = np.isfinite(y)
    x, y = x[known], y[known]
    selected_event_ids = [str(event_id) for event_id, keep in zip(event_ids, known) if keep]
    if not len(y):
        raise ValueError("point_target_has_no_known_labels")
    if not np.isin(y, (0.0, 1.0)).all():
        raise ValueError("point_target_labels_must_be_binary")
    if set(y.tolist()) != {0.0, 1.0}:
        raise ValueError("point_target_requires_both_classes")
    weights = match_balanced_weights(selected_event_ids)
    method = str(method).strip().casefold()
    if method == "lr":
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        model = Pipeline([
            ("scaler", StandardScaler()),
            ("classifier", LogisticRegression(C=1.0, max_iter=1000, random_state=int(seed))),
        ])
        model.fit(x, y.astype(np.int64), classifier__sample_weight=weights)
    elif method == "gb":
        from sklearn.ensemble import HistGradientBoostingClassifier

        model = HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=80,
            max_leaf_nodes=15,
            l2_regularization=1.0,
            random_state=int(seed),
        )
        model.fit(x, y.astype(np.int64), sample_weight=weights)
    else:
        raise ValueError("unsupported_point_baseline_method")
    metadata = {
        "model_type": f"tennis_point_{method}",
        "feature_version": ODDS_FREE_FEATURE_VERSION,
        "feature_names": list(ODDS_FREE_FEATURE_NAMES),
        "target": "point",
        "seed": int(seed),
        "known_examples": int(len(y)),
        "unique_matches": len(set(selected_event_ids)),
    }
    return model, metadata


def save_point_baseline_checkpoint(path: str, model: object, metadata: dict) -> None:
    import joblib

    if metadata.get("feature_version") != ODDS_FREE_FEATURE_VERSION:
        raise ValueError("point_checkpoint_feature_version_mismatch")
    if metadata.get("feature_names") != list(ODDS_FREE_FEATURE_NAMES):
        raise ValueError("point_checkpoint_feature_names_mismatch")
    joblib.dump({"metadata": dict(metadata), "model": model}, path)


def load_point_baseline_checkpoint(path: str) -> tuple[object, dict]:
    import joblib

    payload = joblib.load(path)
    metadata = payload.get("metadata") or {}
    if metadata.get("feature_version") != ODDS_FREE_FEATURE_VERSION:
        raise ValueError("point_checkpoint_feature_version_mismatch")
    if metadata.get("feature_names") != list(ODDS_FREE_FEATURE_NAMES):
        raise ValueError("point_checkpoint_feature_names_mismatch")
    if metadata.get("target") != "point" or metadata.get("model_type") not in {
        "tennis_point_lr", "tennis_point_gb"
    }:
        raise ValueError("point_checkpoint_contract_mismatch")
    return payload["model"], metadata
