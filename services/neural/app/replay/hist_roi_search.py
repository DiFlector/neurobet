"""Two later tennis HistGradientBoosting lines.

The tuned line keeps the v3.1.0 split: fit on the earliest 70 percent and
score every candidate on the last 15 percent. The epoch line chooses the tree
count on that same tail, then follows one hyperparameter change at a time.
Neither line sees the reserved replay matches.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger("neural.hist_roi_search")

TUNED_NAME = "tennis_hist_gradient_roi_tuned"
TUNED_TAG = "tennis_v3.2.0"
EPOCH_NAME = "tennis_hist_gradient_roi_epoch"
EPOCH_TAG = "tennis_v3.3.0"
RELEASE_NAME = "tennis_hist_gradient_roi"
RELEASE_TAG = "tennis_v3.3.1"
MIN_DELTA = 0.0001
PATIENCE = 8
MAX_EPOCHS = 500


def initial_hist_params() -> Dict[str, Any]:
    """The fixed settings used by tennis_hist_gradient_roi tennis_v3.1.0."""
    return {
        "learning_rate": 0.05,
        "max_depth": 4,
        "min_samples_leaf": 20,
        "l2_regularization": 0.0,
        "max_iter": 80,
    }


def tuned_neighbors(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One-step changes around the fixed-iteration HistGradientBoosting fit."""
    found = []
    rate = float(params["learning_rate"])
    if rate / 2.0 >= 0.01:
        found.append({**params, "learning_rate": round(rate / 2.0, 6)})
    depth = int(params["max_depth"])
    for nxt in (depth - 1, depth + 1):
        if 3 <= nxt <= 6:
            found.append({**params, "max_depth": nxt})
    leaves = [20, 50, 100]
    leaf = int(params["min_samples_leaf"])
    if leaf in leaves and leaves.index(leaf) + 1 < len(leaves):
        found.append({**params, "min_samples_leaf": leaves[leaves.index(leaf) + 1]})
    penalties = [0.0, 1.0, 10.0]
    penalty = float(params["l2_regularization"])
    if penalty in penalties and penalties.index(penalty) + 1 < len(penalties):
        found.append({**params, "l2_regularization": penalties[penalties.index(penalty) + 1]})
    iterations = [40, 80, 120, 160]
    current = int(params["max_iter"])
    if current in iterations:
        index = iterations.index(current)
        if index > 0:
            found.append({**params, "max_iter": iterations[index - 1]})
        if index + 1 < len(iterations):
            found.append({**params, "max_iter": iterations[index + 1]})
    return found


def epoch_neighbors(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The v3.0.0 boosting moves. Tree count stays with early stopping."""
    from ..epoch_fit import neighbors

    return neighbors("gb", params)


def _key(params: Dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, default=str)


def _better(candidate: Dict[str, Any], champion: Dict[str, Any]) -> bool:
    loss = float(candidate["log_loss"])
    best_loss = float(champion["log_loss"])
    if loss < best_loss - MIN_DELTA:
        return True
    return abs(loss - best_loss) <= 1e-12 and float(candidate["brier"]) < float(champion["brier"])


def search_until_plateau(
    initial: Dict[str, Any],
    evaluate: Callable[[Dict[str, Any]], Dict[str, Any]],
    neighbor_fn: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Adopt a neighbor only when the same validation slice improves."""
    current = dict(initial)
    visited = {_key(current)}
    champion = evaluate(current)
    champion["params"] = dict(current)
    history = [_public_trial(champion)]
    while True:
        better = []
        for candidate in neighbor_fn(champion["params"]):
            marker = _key(candidate)
            if marker in visited:
                continue
            visited.add(marker)
            trial = evaluate(candidate)
            trial["params"] = dict(candidate)
            history.append(_public_trial(trial))
            if _better(trial, champion):
                better.append(trial)
        if not better:
            break
        champion = min(better, key=lambda trial: (float(trial["log_loss"]), float(trial["brier"])))
        logger.info(
            "Adopted hist params=%s log_loss=%.4f brier=%.4f",
            json.dumps(champion["params"], sort_keys=True),
            float(champion["log_loss"]),
            float(champion["brier"]),
        )
    champion["visited"] = history
    return champion


def _public_trial(trial: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "params": dict(trial["params"]),
        "log_loss": float(trial["log_loss"]),
        "brier": float(trial["brier"]),
        "best_epoch": int(trial.get("best_epoch") or trial["params"].get("max_iter") or 0),
        "reason": trial.get("reason"),
    }


def make_hist(params: Dict[str, Any], max_iter: Optional[int] = None):
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(
        learning_rate=float(params["learning_rate"]),
        max_iter=int(max_iter if max_iter is not None else params["max_iter"]),
        max_depth=int(params["max_depth"]),
        min_samples_leaf=int(params["min_samples_leaf"]),
        l2_regularization=float(params["l2_regularization"]),
        random_state=42,
    )


def _fit(model, rows, labels, weights):
    model.fit(rows, labels, sample_weight=weights)
    return model


def score_match_rows(model, matches: Sequence[Any], names: Sequence[str]) -> Dict[str, float]:
    from .booster_compare import rank_metrics

    return rank_metrics(model, matches, names)


def fit_fixed(base: Sequence[Any], names: Sequence[str], params: Dict[str, Any]):
    from .booster_compare import match_rows

    rows, labels, weights = match_rows(base, names)
    return _fit(make_hist(params), rows, labels, weights)


def epoch_loop(train: Sequence[Any], validation: Sequence[Any], names: Sequence[str], params: Dict[str, Any]) -> Dict[str, Any]:
    """Add one tree at a time and stop after eight validations without improvement."""
    from ..dataset import positive_probabilities
    from ..metrics import compute_classification_metrics
    from .booster_compare import match_rows

    rows, labels, weights = match_rows(train, names)
    val_rows, val_labels, _weights = match_rows(validation, names)
    model = make_hist(params, max_iter=1)
    model.set_params(warm_start=True)
    best_loss = None
    best_brier = None
    best_epoch = 0
    waited = 0
    curve = []
    reason = "max_epochs"
    for epoch in range(1, MAX_EPOCHS + 1):
        model.set_params(max_iter=epoch)
        model.fit(rows, labels, sample_weight=weights)
        probability = np.clip(positive_probabilities(model, val_rows), 1e-6, 1 - 1e-6)
        metrics = compute_classification_metrics(val_labels, probability)
        loss = float(metrics["log_loss"])
        brier = float(metrics["brier_score"])
        if best_loss is None or loss < best_loss - MIN_DELTA or (abs(loss - best_loss) <= 1e-12 and brier < best_brier):
            best_loss, best_brier, best_epoch, waited = loss, brier, epoch, 0
        else:
            waited += 1
        curve.append({"epoch": epoch, "log_loss": round(loss, 6), "brier": round(brier, 6), "waited": waited})
        logger.info(
            "Hist epoch params=%s epoch=%d log_loss=%.4f brier=%.4f best_epoch=%d waited=%d",
            json.dumps({key: params[key] for key in ("learning_rate", "max_depth", "min_samples_leaf", "l2_regularization")}, sort_keys=True),
            epoch,
            loss,
            brier,
            best_epoch,
            waited,
        )
        if waited >= PATIENCE:
            reason = "validation_plateau"
            break
    return {
        "log_loss": float(best_loss),
        "brier": float(best_brier),
        "best_epoch": int(best_epoch),
        "reason": reason,
        "curve": curve,
        "params": dict(params),
    }


def walk_forward(matches: Sequence[Any], names: Sequence[str], fit_one: Callable[[Sequence[Any]], Any]) -> Dict[str, Any]:
    """Score confirming match decisions with a model that has seen only earlier matches."""
    from ..dataset import chronological_folds, decision_records
    from ..metrics import compute_classification_metrics, insufficient_metrics

    ordered = [match for match in matches if match.rows]
    mode, folds = chronological_folds(len(ordered))
    records = []
    for train_index, test_index in folds:
        model = fit_one([ordered[index] for index in train_index])
        fold_records = decision_records(model, [ordered[index] for index in test_index], names, "tennis")
        records.extend(row for row in fold_records if row.get("market") == "match_winner")
    labels = [int(row["label"]) for row in records]
    probabilities = [float(row["probability"]) for row in records]
    if labels and len(set(labels)) > 1:
        metrics = compute_classification_metrics(np.asarray(labels, dtype=int), np.asarray(probabilities, dtype=float))
    else:
        metrics = insufficient_metrics()
    return {"validation": mode, "decision_rows": len(records), **metrics}


def register_line(
    session,
    name: str,
    version_tag: str,
    model,
    names: Sequence[str],
    metrics: Dict[str, Any],
    params: Dict[str, Any],
    replace: bool = False,
) -> str:
    from datetime import datetime, timezone
    import os

    import joblib
    from db.models.ml_registry import ModelVersion

    existing = (
        session.query(ModelVersion)
        .filter(
            ModelVersion.model_name == name,
            ModelVersion.version_tag == version_tag,
            ModelVersion.sport_code == "tennis",
            ModelVersion.market == "match_winner",
        )
        .one_or_none()
    )
    directory = os.getenv("CHECKPOINT_DIR", "/app/models/checkpoints")
    path = os.path.join(directory, f"tennis_match_winner_{name}_{version_tag}.joblib")
    if existing is not None and not replace:
        return str(existing.id)
    joblib.dump(
        {
            "model": model,
            "feature_names": list(names),
            "model_name": name,
            "version_tag": version_tag,
            "sport_code": "tennis",
            "market": "match_winner",
            "feature_version": "1.1.0",
            "metrics_val": metrics,
            "hyperparameters": params,
            "trained_at": datetime.now(timezone.utc).isoformat(),
        },
        path,
    )
    if existing is not None:
        existing.artifact_path = path
        existing.metrics = metrics
        existing.hyperparameters = params
        existing.status = "SHADOW"
        session.commit()
        return str(existing.id)
    row = ModelVersion(
        model_name=name,
        version_tag=version_tag,
        sport_code="tennis",
        market="match_winner",
        artifact_path=path,
        metrics=metrics,
        hyperparameters=params,
        status="SHADOW",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return str(row.id)


def _stored_params(trial: Dict[str, Any]) -> Dict[str, Any]:
    public = dict(trial["params"])
    public["search"] = {
        "log_loss": float(trial["log_loss"]),
        "brier": float(trial["brier"]),
        "best_epoch": int(trial.get("best_epoch") or 0),
        "reason": trial.get("reason"),
        "visited": trial.get("visited") or [],
        "curve": trial.get("curve") or [],
    }
    return public


def run(session, only: str = "all") -> Dict[str, Any]:
    from ..dataset import SportDatasetBuilder, thin_match
    from .booster_compare import split_train
    from .strategy_job import checkpoint_dir, prepare_windows, write_json

    windows = prepare_windows(session)
    reserved = set(windows["gate_final"]) | set(windows["model_final"]) | set(windows["search"]) | set(windows["blocked"])
    builder = SportDatasetBuilder("tennis")
    loaded, names = builder.load_matches(session)
    trainable = [thin_match(match) for match in loaded if match.event_id in set(windows["train"]) and match.rows]
    leaked = [match.event_id for match in trainable if match.event_id in reserved]
    if leaked:
        raise RuntimeError(f"training matches overlap reserved windows: {leaked[:5]}")
    base, blend, validation = split_train(trainable)
    logger.info("Hist ROI split base=%d blend=%d validation=%d", len(base), len(blend), len(validation))

    def evaluate_tuned(params: Dict[str, Any]) -> Dict[str, Any]:
        model = fit_fixed(base, names, params)
        metrics = score_match_rows(model, validation, names)
        logger.info("Tuned trial %s -> log_loss=%s brier=%s", json.dumps(params, sort_keys=True), metrics["log_loss"], metrics["brier_score"])
        return {"log_loss": metrics["log_loss"], "brier": metrics["brier_score"], "reason": "fixed_iterations"}

    previous = _previous_report()
    if only == "epoch" and previous.get("tuned"):
        tuned_report = previous["tuned"]
    else:
        tuned = search_until_plateau(initial_hist_params(), evaluate_tuned, tuned_neighbors)
        tuned_model = fit_fixed(base, names, tuned["params"])
        tuned_validation = score_match_rows(tuned_model, validation, names)
        tuned_forward = walk_forward(trainable, names, lambda matches: fit_fixed(matches, names, tuned["params"]))
        tuned_metrics = {**tuned_validation, "walk_forward": tuned_forward}
        tuned_id = register_line(session, TUNED_NAME, TUNED_TAG, tuned_model, names, tuned_metrics, _stored_params(tuned))
        tuned_report = {
            "model_name": TUNED_NAME,
            "version_tag": TUNED_TAG,
            "model_version_id": tuned_id,
            "metrics": tuned_metrics,
            "params": tuned["params"],
        }

    def evaluate_epoch(params: Dict[str, Any]) -> Dict[str, Any]:
        report = epoch_loop(base, validation, names, params)
        logger.info(
            "Epoch trial %s -> log_loss=%.4f brier=%.4f best_epoch=%d reason=%s",
            json.dumps(params, sort_keys=True),
            report["log_loss"],
            report["brier"],
            report["best_epoch"],
            report["reason"],
        )
        return report

    epoch_start = initial_hist_params()
    found = search_until_plateau(epoch_start, evaluate_epoch, epoch_neighbors)
    epoch_params = dict(found["params"])
    epoch_params["max_iter"] = int(found["best_epoch"])
    epoch_model = fit_fixed(base + list(blend) + list(validation), names, epoch_params)
    epoch_validation = {
        "log_loss": round(float(found["log_loss"]), 4),
        "brier_score": round(float(found["brier"]), 4),
        "rows": score_match_rows(epoch_model, validation, names)["rows"],
        "best_epoch": int(found["best_epoch"]),
        "reason": found.get("reason"),
    }
    epoch_forward = walk_forward(
        trainable,
        names,
        lambda matches, frozen=dict(epoch_params): fit_fixed(matches, names, frozen),
    )
    epoch_metrics = {**epoch_validation, "walk_forward": epoch_forward}
    epoch_id = register_line(
        session,
        EPOCH_NAME,
        EPOCH_TAG,
        epoch_model,
        names,
        epoch_metrics,
        _stored_params(found) | {"max_iter": epoch_params["max_iter"], "max_epochs": MAX_EPOCHS},
        replace=True,
    )
    report = {
        "baseline": {"model_name": "tennis_hist_gradient_roi", "version_tag": "tennis_v3.1.0"},
        "reserved_matches": {key: len(windows[key]) for key in ("gate_final", "model_final", "search", "blocked")},
        "training_matches": len(trainable),
        "tuned": tuned_report,
        "epoch": {"model_name": EPOCH_NAME, "version_tag": EPOCH_TAG, "model_version_id": epoch_id, "metrics": epoch_metrics, "params": epoch_params},
    }
    write_json("hist_roi_search_report.json", report)
    logger.info("Hist ROI report written to %s", checkpoint_dir())
    return report


def run_release(session) -> Dict[str, Any]:
    """Train tennis_hist_gradient_roi tennis_v3.3.1 on every finished match, including the newest."""
    from ..dataset import SportDatasetBuilder, thin_match
    from .booster_compare import split_train
    from .strategy_job import checkpoint_dir, write_json

    builder = SportDatasetBuilder("tennis")
    loaded, names = builder.load_matches(session)
    trainable = [thin_match(match) for match in loaded if match.rows]
    base, blend, validation = split_train(trainable)
    logger.info("Release %s split base=%d blend=%d validation=%d", RELEASE_TAG, len(base), len(blend), len(validation))

    def evaluate_epoch(params: Dict[str, Any]) -> Dict[str, Any]:
        report = epoch_loop(base, validation, names, params)
        logger.info(
            "Epoch trial %s -> log_loss=%.4f brier=%.4f best_epoch=%d reason=%s",
            json.dumps(params, sort_keys=True),
            report["log_loss"],
            report["brier"],
            report["best_epoch"],
            report["reason"],
        )
        return report

    found = search_until_plateau(initial_hist_params(), evaluate_epoch, epoch_neighbors)
    if found.get("reason") == "max_epochs":
        raise RuntimeError(f"{RELEASE_TAG} stopped at the epoch ceiling while validation was still improving")
    epoch_params = dict(found["params"])
    epoch_params["max_iter"] = int(found["best_epoch"])
    model = fit_fixed(trainable, names, epoch_params)
    validation_metrics = {
        "log_loss": round(float(found["log_loss"]), 4),
        "brier_score": round(float(found["brier"]), 4),
        "rows": score_match_rows(model, validation, names)["rows"],
        "best_epoch": int(found["best_epoch"]),
        "reason": found.get("reason"),
    }
    forward = walk_forward(trainable, names, lambda matches, frozen=dict(epoch_params): fit_fixed(matches, names, frozen))
    metrics = {**validation_metrics, "walk_forward": forward}
    model_id = register_line(
        session,
        RELEASE_NAME,
        RELEASE_TAG,
        model,
        names,
        metrics,
        _stored_params(found) | {"max_iter": epoch_params["max_iter"], "max_epochs": MAX_EPOCHS},
        replace=False,
    )
    report = {
        "model_name": RELEASE_NAME,
        "version_tag": RELEASE_TAG,
        "model_version_id": model_id,
        "training_matches": len(trainable),
        "metrics": metrics,
        "params": epoch_params,
    }
    write_json("hist_roi_v331_report.json", report)
    logger.info("Release report written to %s", checkpoint_dir())
    return report


def _previous_report() -> Dict[str, Any]:
    import os

    from .strategy_job import checkpoint_dir

    path = os.path.join(checkpoint_dir(), "hist_roi_search_report.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main() -> None:
    import argparse

    from db.connection import SessionLocal

    parser = argparse.ArgumentParser(description="Train the two HistGradientBoosting ROI lines")
    parser.add_argument("--only", choices=("all", "epoch", "release"), default="all")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with SessionLocal() as session:
        if args.only == "release":
            run_release(session)
        else:
            run(session, only=args.only)


if __name__ == "__main__":
    main()
