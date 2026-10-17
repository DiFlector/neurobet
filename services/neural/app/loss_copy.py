"""Train loss copies without touching the lines that already exist.

Each copy has its own model name, its own checkpoint, and a test version tag.
A test tag is not loaded for live inference and is not promoted.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

import joblib
import numpy as np

from .commit import side_odds_from_row
from .dataset import (
    chronological_folds,
    decision_records,
    get_dataset_builder,
    positive_probabilities,
    stack_matches,
    thin_match,
)
from .metrics import (
    compute_classification_metrics,
    copy_beats_baseline,
    error_summary,
    insufficient_metrics,
    loss_adjustment,
)
from .models import (
    _holdout_cut,
    build_gradient_boosting_pipeline,
    build_logistic_regression_pipeline,
)
from .trainer import _log_loss_gap, get_git_commit_hash, training_lock_key
from .versioning import allocate_test_version

logger = logging.getLogger("neural.loss_copy")

COPIES = (
    {
        "source": "tennis_logistic_regression",
        "model_name": "tennis_logistic_regression_loss",
        "kind": "lr",
    },
    {
        "source": "tennis_gradient_boosting",
        "model_name": "tennis_gradient_boosting_loss",
        "kind": "gb",
    },
    {
        "source": "tennis_composite",
        "model_name": "tennis_residual_mlp_loss",
        "kind": "mlp",
    },
)
GAMMAS = (0.0, 0.5, 1.0)
MARKET = "match_winner"


class TorchProbabilityModel:
    """predict_proba wrapper around a residual MLP bundle. Live loading skips its test tag."""

    def __init__(self, bundle: Dict[str, Any], feature_names: Sequence[str], residual: bool = True):
        self.bundle = bundle
        self.feature_names = list(feature_names)
        self.residual = residual
        self.classes_ = np.asarray([0, 1])

    def predict_proba(self, rows: np.ndarray) -> np.ndarray:
        from .compare.feature_models import _mlp_predict

        matrix = np.asarray(rows, dtype=float)
        odds = np.asarray([side_odds_from_row(row, self.feature_names) for row in matrix], dtype=float)
        device = self.bundle["device"]
        probs = _mlp_predict(
            self.bundle["model"],
            self.bundle["scaler"],
            matrix,
            odds,
            device,
            self.residual,
        )
        clipped = np.clip(np.asarray(probs, dtype=float), 1e-6, 1.0 - 1e-6)
        return np.column_stack([1.0 - clipped, clipped])


def _pack(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary = error_summary(records)
    labels = [int(row["label"]) for row in records]
    probs = [float(row["probability"]) for row in records]
    if labels and len(set(labels)) > 1:
        metrics = compute_classification_metrics(np.asarray(labels, dtype=int), np.asarray(probs, dtype=float))
    else:
        metrics = insufficient_metrics()
    return {
        "critical_miss_rate": summary["critical_miss_rate"],
        "brier_score": float(metrics.get("brier_score") or 0.0),
        "log_loss": float(metrics.get("log_loss") or 0.0),
        "summary": summary,
        "metrics": metrics,
        "records": records,
    }


def _walk(factory: Callable, matches: Sequence[Any], names: Sequence[str], sport_code: str) -> Dict[str, Any]:
    _mode, folds = chronological_folds(len(matches))
    records: List[Dict[str, Any]] = []
    for train_index, test_index in folds:
        train = [thin_match(matches[index]) for index in train_index]
        test = [thin_match(matches[index]) for index in test_index]
        model = factory(train)
        records.extend(decision_records(model, test, names, sport_code))
    return _pack(records)


def _last_fold(matches: Sequence[Any]):
    _mode, folds = chronological_folds(len(matches))
    train_index, test_index = folds[-1]
    train = [thin_match(matches[index]) for index in train_index]
    test = [thin_match(matches[index]) for index in test_index]
    saved = train
    return saved, test


def _train_log_loss(model: Any, saved: Sequence[Any]) -> Dict[str, float]:
    fraction = float(getattr(model, "calibration_fraction", 0.2) or 0.2)
    cut = _holdout_cut(len(saved), fraction)
    rows, labels, _weights = stack_matches(list(saved[:cut] or saved))
    if len(rows) < 8 or len(set(labels.tolist())) < 2:
        return insufficient_metrics()
    return compute_classification_metrics(labels, positive_probabilities(model, rows))


def _sklearn_factory(kind: str, names: Sequence[str], calibration: str, spec: Dict[str, Any], gamma: float):
    def factory(train):
        if kind == "lr":
            model = build_logistic_regression_pipeline(
                C=float(spec["C"]),
                random_state=42,
                calibration_method=calibration,
            )
        else:
            model = build_gradient_boosting_pipeline(
                learning_rate=0.05,
                max_iter=100,
                max_depth=4,
                random_state=42,
                min_samples_leaf=int(spec["min_samples_leaf"]),
                l2_regularization=float(spec["l2_regularization"]),
                calibration_method=calibration,
            )
        model.fit_matches(train, names, loss_gamma=gamma)
        return model

    return factory


def _mlp_factory(names: Sequence[str], dropout: float, weight_decay: float, gamma: float):
    def factory(train):
        from .compare.feature_models import _fit_mlp

        cut = _holdout_cut(len(train), 0.2)
        earlier = list(train[:cut] or train)
        later = list(train[cut:] or earlier)
        bundle = _fit_mlp(earlier, later, names, (64, 32), dropout, weight_decay, 25, True, gamma)
        bundle["model"].cpu()
        bundle["device"] = __import__("torch").device("cpu")
        return TorchProbabilityModel(bundle, names, True)

    return factory


def _candidates(kind: str, adjustment: str) -> List[Dict[str, Any]]:
    if adjustment == "regularize" and kind == "lr":
        return [{"C": value, "gamma": 0.0} for value in (1.0, 0.1, 0.01)]
    if adjustment == "regularize" and kind == "gb":
        specs = []
        for l2 in (0.0, 1.0, 10.0):
            for leaf in (5, 20):
                specs.append({"l2_regularization": l2, "min_samples_leaf": leaf, "gamma": 0.0})
        return specs
    if adjustment == "regularize" and kind == "mlp":
        specs = []
        for dropout in (0.1, 0.3):
            for decay in (1e-3, 1e-2):
                specs.append({"dropout": dropout, "weight_decay": decay, "gamma": 0.0})
        return specs
    if adjustment == "gamma":
        if kind == "lr":
            return [{"C": 1.0, "gamma": gamma} for gamma in GAMMAS]
        if kind == "gb":
            return [{"l2_regularization": 0.0, "min_samples_leaf": 5, "gamma": gamma} for gamma in GAMMAS]
        return [{"dropout": 0.1, "weight_decay": 1e-3, "gamma": gamma} for gamma in GAMMAS]
    return []


def _factory_for(kind: str, names: Sequence[str], calibration: str, spec: Dict[str, Any]):
    gamma = float(spec.get("gamma") or 0.0)
    if kind == "mlp":
        return _mlp_factory(names, float(spec["dropout"]), float(spec["weight_decay"]), gamma)
    return _sklearn_factory(kind, names, calibration, spec, gamma)


def _baseline_spec(kind: str) -> Dict[str, Any]:
    if kind == "lr":
        return {"C": 1.0, "gamma": 0.0}
    if kind == "gb":
        return {"l2_regularization": 0.0, "min_samples_leaf": 5, "gamma": 0.0}
    return {"dropout": 0.1, "weight_decay": 1e-3, "gamma": 0.0}


def _fold_loss(factory: Callable, train, test, names, sport_code: str) -> float:
    model = factory(train)
    packed = _pack(decision_records(model, test, names, sport_code))
    if packed["metrics"].get("insufficient_validation"):
        return 1.0
    return float(packed["log_loss"])


def _public(packed: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "critical_miss_rate": packed["critical_miss_rate"],
        "brier_score": packed["brier_score"],
        "log_loss": packed["log_loss"],
        "summary": packed["summary"],
        "metrics": packed["metrics"],
    }


def run_copy(session, kind: str, model_name: str, source: str, sport_code: str, checkpoint_dir: str) -> Dict[str, Any]:
    """Fit one copy. The source line's rows and files are not updated."""
    from sqlalchemy import text

    from db.models import ModelVersion, TrainingDataset, TrainingRun

    lock_key = training_lock_key(sport_code, model_name, MARKET)
    acquired = session.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}).scalar()
    if not acquired:
        return {"model_name": model_name, "registered": False, "reason": "already_running"}
    try:
        matches, names = get_dataset_builder(sport_code).load_matches(session)
        calibration = "sigmoid"
        if kind == "gb" and len(matches) >= 1000:
            calibration = "isotonic"
        baseline_spec = _baseline_spec(kind)
        baseline_factory = _factory_for(kind, names, calibration, baseline_spec)
        logger.info("Walk-forward baseline for %s", model_name)
        baseline = _walk(baseline_factory, matches, names, sport_code)
        _mode, folds = chronological_folds(len(matches))
        saved = [thin_match(matches[index]) for index in folds[-1][0]]
        trained = baseline_factory(saved)
        train_metrics = _train_log_loss(trained, saved)
        gap = _log_loss_gap(train_metrics, baseline["metrics"])
        adjustment = "none" if gap is None else loss_adjustment(gap, baseline["summary"])
        report: Dict[str, Any] = {
            "model_name": model_name,
            "source": source,
            "registered": False,
            "adjustment": adjustment,
            "log_loss_gap": gap,
            "baseline": _public(baseline),
            "reason": "",
        }
        if adjustment == "none":
            report["reason"] = "no_loss_change"
            return report
        fold_train, fold_test = _last_fold(matches)
        best_spec = None
        best_loss = None
        trials = []
        for spec in _candidates(kind, adjustment):
            loss = _fold_loss(_factory_for(kind, names, calibration, spec), fold_train, fold_test, names, sport_code)
            trials.append({**spec, "log_loss": round(loss, 6)})
            logger.info("Trial %s %s log_loss=%.4f", model_name, spec, loss)
            if best_loss is None or loss < best_loss:
                best_loss = loss
                best_spec = spec
        report["trials"] = trials
        report["chosen"] = best_spec
        logger.info("Walk-forward candidate %s %s", model_name, best_spec)
        candidate = _walk(_factory_for(kind, names, calibration, best_spec), matches, names, sport_code)
        report["candidate"] = _public(candidate)
        if not copy_beats_baseline(report["baseline"], report["candidate"]):
            report["reason"] = "not_better"
            return report
        final = _factory_for(kind, names, calibration, best_spec)(saved)
        existing = [
            row[0]
            for row in session.query(ModelVersion.version_tag)
            .filter(
                ModelVersion.sport_code == sport_code,
                ModelVersion.model_name == model_name,
                ModelVersion.market == MARKET,
            )
            .all()
        ]
        version = allocate_test_version(existing, sport_code)
        os.makedirs(checkpoint_dir, exist_ok=True)
        artifact_path = os.path.join(checkpoint_dir, f"{sport_code}_{MARKET}_{model_name}_{version}.joblib")
        hyperparameters = {
            **best_spec,
            "source_model_name": source,
            "calibration": calibration if kind != "mlp" else "early_stopping",
            "loss_adjustment": adjustment,
            "git_commit": get_git_commit_hash(),
        }
        joblib.dump(
            {
                "model": final,
                "feature_names": list(names),
                "model_name": model_name,
                "version_tag": version,
                "sport_code": sport_code,
                "market": MARKET,
                "hyperparameters": hyperparameters,
                "metrics_val": candidate["metrics"],
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "kind": "loss_copy",
            },
            artifact_path,
        )
        started = datetime.now(timezone.utc)
        dataset = TrainingDataset(
            name=f"{model_name}_{version}",
            sport_code=sport_code,
            date_from=matches[0].started_at,
            date_to=matches[-1].started_at,
            row_count=int(baseline["summary"]["decisions"] + candidate["summary"]["decisions"]),
            storage_path=artifact_path,
        )
        session.add(dataset)
        session.flush()
        version_row = ModelVersion(
            model_name=model_name,
            version_tag=version,
            sport_code=sport_code,
            market=MARKET,
            artifact_path=artifact_path,
            metrics=candidate["metrics"],
            hyperparameters=hyperparameters,
            status="SHADOW",
        )
        session.add(version_row)
        session.flush()
        session.add(
            TrainingRun(
                model_version_id=version_row.id,
                dataset_id=dataset.id,
                sport_code=sport_code,
                status="completed",
                metrics_train=train_metrics,
                metrics_val=candidate["metrics"],
                started_at=started,
                finished_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
        report["registered"] = True
        report["version_tag"] = version
        report["artifact_path"] = artifact_path
        report["reason"] = "registered"
        return report
    finally:
        try:
            session.rollback()
            session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
        except Exception:
            logger.exception("Could not release the training lock for %s", model_name)


def main() -> None:
    import argparse

    from db.connection import SessionLocal

    parser = argparse.ArgumentParser(description="Train a loss copy without changing the current lines")
    parser.add_argument("--kind", choices=["lr", "gb", "mlp", "all"], default="all")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    checkpoint_dir = os.getenv("NEUROBET_CHECKPOINT_DIR", "/app/models/checkpoints")
    selected = [item for item in COPIES if args.kind == "all" or item["kind"] == args.kind]
    reports = []
    for copy in selected:
        with SessionLocal() as session:
            report = run_copy(
                session,
                kind=copy["kind"],
                model_name=copy["model_name"],
                source=copy["source"],
                sport_code="tennis",
                checkpoint_dir=checkpoint_dir,
            )
        reports.append({key: value for key, value in report.items() if key != "records"})
        logger.info("Copy %s: %s", copy["model_name"], report.get("reason"))
    name = "loss_copy_report.json" if args.kind == "all" else f"loss_copy_{args.kind}_report.json"
    path = os.path.join(checkpoint_dir, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(reports, handle, ensure_ascii=False, indent=2, default=str)
    logger.info("Wrote %s", path)


if __name__ == "__main__":
    main()
