"""Compare extra tennis models after the stake gates are frozen.

Training uses only matches older than the gate-search window. Validation is the
latest slice of that history. The model-final hundred is scored once, with the
gates already chosen.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger("neural.booster_compare")
VERSION_TAG = "tennis_v3.1.0"


def split_train(matches: Sequence[Any]) -> tuple:
    """Base fit, blender fit, and an untouched validation tail. Whole matches only."""
    ordered = sorted(matches, key=lambda item: item.started_at)
    count = len(ordered)
    base_end = max(1, int(count * 0.70))
    blend_end = max(base_end + 1, int(count * 0.85))
    if blend_end >= count:
        blend_end = count - 1
    return ordered[:base_end], ordered[base_end:blend_end], ordered[blend_end:]


def match_rows(matches: Sequence[Any], names: Sequence[str]):
    from ..compare.feature_models import stack

    rows, labels, weights, markets, _odds = stack(matches, names)
    indexes = [index for index, market in enumerate(markets) if market == "match_winner"]
    if not indexes:
        width = len(names)
        return np.empty((0, width)), np.empty((0,), dtype=int), np.empty((0,))
    picked = np.asarray(indexes)
    return rows[picked], labels[picked], weights[picked]


def rank_metrics(model, matches: Sequence[Any], names: Sequence[str]) -> Dict[str, float]:
    rows, labels, _weights = match_rows(matches, names)
    if len(labels) < 2 or len(set(labels.tolist())) < 2:
        return {"log_loss": 1.0, "brier_score": 1.0, "rows": int(len(labels))}
    probability = np.clip(model.predict_proba(rows)[:, 1], 1e-6, 1 - 1e-6)
    return {
        "log_loss": round(float(log_loss(labels, probability, labels=[0, 1])), 4),
        "brier_score": round(float(brier_score_loss(labels, probability)), 4),
        "rows": int(len(labels)),
    }


def _positive_fit(model, rows, labels, weights) -> Any:
    if isinstance(model, Pipeline):
        step = model.steps[-1][0]
        model.fit(rows, labels, **{f"{step}__sample_weight": weights})
    else:
        model.fit(rows, labels, sample_weight=weights)
    return model


def fit_logistic(base, blend, names):
    rows, labels, weights = match_rows(base, names)
    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(C=1.0, max_iter=300, solver="lbfgs")),
        ]
    )
    return _positive_fit(model, rows, labels, weights)


def fit_hist_gradient(base, blend, names):
    from sklearn.ensemble import HistGradientBoostingClassifier

    rows, labels, weights = match_rows(base, names)
    model = HistGradientBoostingClassifier(
        max_iter=80,
        learning_rate=0.05,
        max_depth=4,
        min_samples_leaf=20,
        random_state=42,
    )
    model.fit(rows, labels, sample_weight=weights)
    return model


def fit_mlp(base, blend, names):
    from sklearn.neural_network import MLPClassifier

    rows, labels, weights = match_rows(base, names)
    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "model",
                MLPClassifier(
                    hidden_layer_sizes=(64, 32),
                    alpha=1e-3,
                    max_iter=40,
                    random_state=42,
                    early_stopping=True,
                ),
            ),
        ]
    )
    return _positive_fit(model, rows, labels, weights)


def fit_catboost(base, blend, names):
    try:
        from catboost import CatBoostClassifier
    except ImportError:
        return None
    rows, labels, weights = match_rows(base, names)
    hold_rows, hold_labels, _weights = match_rows(blend, names)
    model = CatBoostClassifier(
        iterations=400,
        learning_rate=0.05,
        depth=6,
        loss_function="Logloss",
        random_seed=42,
        thread_count=2,
        verbose=False,
        allow_writing_files=False,
        early_stopping_rounds=30,
    )
    model.fit(rows, labels, sample_weight=weights, eval_set=(hold_rows, hold_labels), use_best_model=True)
    return model


def fit_lightgbm(base, blend, names):
    try:
        from lightgbm import LGBMClassifier, early_stopping
    except ImportError:
        return None
    rows, labels, weights = match_rows(base, names)
    hold_rows, hold_labels, _weights = match_rows(blend, names)
    model = LGBMClassifier(
        n_estimators=400,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        n_jobs=2,
        verbose=-1,
    )
    model.fit(
        rows,
        labels,
        sample_weight=weights,
        eval_set=[(hold_rows, hold_labels)],
        callbacks=[early_stopping(30, verbose=False)],
    )
    return model


def fit_xgboost(base, blend, names):
    try:
        from xgboost import XGBClassifier
    except ImportError:
        return None
    rows, labels, weights = match_rows(base, names)
    hold_rows, hold_labels, _weights = match_rows(blend, names)
    model = XGBClassifier(
        n_estimators=400,
        learning_rate=0.05,
        max_depth=4,
        min_child_weight=5,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        random_state=42,
        n_jobs=2,
        early_stopping_rounds=30,
        eval_metric="logloss",
    )
    model.fit(rows, labels, sample_weight=weights, eval_set=[(hold_rows, hold_labels)], verbose=False)
    return model


def fit_stack(base, blend, names):
    """LR, boosting and a small MLP. The blender sees only the later blend matches."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.neural_network import MLPClassifier

    rows, labels, weights = match_rows(base, names)
    hold_rows, hold_labels, _weights = match_rows(blend, names)
    logistic = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(C=1.0, max_iter=200, solver="lbfgs")),
        ]
    )
    boosting = HistGradientBoostingClassifier(max_iter=80, learning_rate=0.05, max_depth=4, min_samples_leaf=20, random_state=42)
    network = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("model", MLPClassifier(hidden_layer_sizes=(64, 32), alpha=1e-3, max_iter=40, random_state=42, early_stopping=True)),
        ]
    )
    _positive_fit(logistic, rows, labels, weights)
    boosting.fit(rows, labels, sample_weight=weights)
    _positive_fit(network, rows, labels, weights)
    bases = [logistic, boosting, network]
    stacked = np.column_stack([item.predict_proba(hold_rows)[:, 1] for item in bases])
    blender = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(C=1.0, max_iter=200, solver="lbfgs")),
        ]
    )
    blender.fit(stacked, hold_labels)
    from ..inference import ProbabilityBlend

    return ProbabilityBlend(bases, blender)


def register_shadow(session, name: str, model, names: Sequence[str], metrics: Dict[str, Any], params: Dict[str, Any]) -> str:
    from db.models.ml_registry import ModelVersion

    existing = (
        session.query(ModelVersion)
        .filter(
            ModelVersion.model_name == name,
            ModelVersion.version_tag == VERSION_TAG,
            ModelVersion.sport_code == "tennis",
            ModelVersion.market == "match_winner",
        )
        .one_or_none()
    )
    if existing is not None:
        return str(existing.id)
    directory = os.getenv("CHECKPOINT_DIR", "/app/models/checkpoints")
    path = os.path.join(directory, f"tennis_match_winner_{name}_{VERSION_TAG}.joblib")
    joblib.dump(
        {
            "model": model,
            "feature_names": list(names),
            "model_name": name,
            "version_tag": VERSION_TAG,
            "sport_code": "tennis",
            "market": "match_winner",
            "feature_version": "1.1.0",
            "metrics_val": metrics,
            "hyperparameters": params,
            "trained_at": datetime.now(timezone.utc).isoformat(),
        },
        path,
    )
    row = ModelVersion(
        model_name=name,
        version_tag=VERSION_TAG,
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


def frozen_gates() -> Dict[str, Any]:
    from .strategy_job import checkpoint_dir, live_gate_params

    path = os.path.join(checkpoint_dir(), "strategy_final_report.json")
    if not os.path.isfile(path):
        return live_gate_params()
    with open(path, encoding="utf-8") as handle:
        report = json.load(handle)
    if report.get("adopted") and report.get("chosen", {}).get("params"):
        return report["chosen"]["params"]
    return live_gate_params()


def main() -> None:
    from db.connection import SessionLocal

    from ..dataset import SportDatasetBuilder, thin_match
    from .strategy_job import checkpoint_dir, prepare_windows, record_match_tape

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with SessionLocal() as session:
        windows = prepare_windows(session)
        reserved = set(windows["gate_final"]) | set(windows["model_final"]) | set(windows["search"]) | set(windows["blocked"])
        builder = SportDatasetBuilder("tennis")
        loaded, names = builder.load_matches(session)
        trainable = [thin_match(match) for match in loaded if match.event_id in set(windows["train"]) and len(match.rows) > 0]
        leaked = [match.event_id for match in trainable if match.event_id in reserved]
        if leaked:
            raise RuntimeError(f"training matches overlap reserved windows: {leaked[:5]}")
        base, blend, validation = split_train(trainable)
        logger.info("Booster split base=%d blend=%d validation=%d", len(base), len(blend), len(validation))
        fitted = []
        for name, fitter in (
            ("tennis_logistic_roi", fit_logistic),
            ("tennis_hist_gradient_roi", fit_hist_gradient),
            ("tennis_mlp_roi", fit_mlp),
            ("tennis_catboost_roi", fit_catboost),
            ("tennis_lightgbm_roi", fit_lightgbm),
            ("tennis_xgboost_roi", fit_xgboost),
            ("tennis_stack_roi", fit_stack),
        ):
            logger.info("Fitting %s", name)
            model = fitter(base, blend, names)
            if model is None:
                fitted.append({"model_name": name, "status": "unavailable"})
                continue
            metrics = rank_metrics(model, validation, names)
            model_id = register_shadow(session, name, model, names, metrics, {"family": name, "version_tag": VERSION_TAG})
            fitted.append({"model_name": name, "status": "SHADOW", "model_version_id": model_id, **metrics})
            logger.info("%s validation log_loss=%s brier=%s", name, metrics["log_loss"], metrics["brier_score"])
        available = [row for row in fitted if row.get("log_loss") is not None]
        available.sort(key=lambda row: (row["log_loss"], row["brier_score"]))
        best = available[0]["log_loss"] if available else None
        finalists = [row for row in available if best is not None and row["log_loss"] <= best + 0.01][:3]
        gates = frozen_gates()
        replays = []
        for row in finalists:
            summary, _candidates, _model = record_match_tape(session, row["model_version_id"], windows["model_final"], gates)
            replays.append(
                {
                    "model_name": row["model_name"],
                    "model_version_id": row["model_version_id"],
                    "validation_log_loss": row["log_loss"],
                    "validation_brier": row["brier_score"],
                    "bets": summary.get("bets"),
                    "wins": summary.get("wins"),
                    "roi": summary.get("roi"),
                    "net_pnl": summary.get("net_pnl"),
                    "max_drawdown": summary.get("max_drawdown"),
                    "bankruptcies": summary.get("bankruptcies"),
                }
            )
        report = {"version_tag": VERSION_TAG, "gates": gates, "lines": fitted, "finalists": replays}
        path = os.path.join(checkpoint_dir(), "booster_report.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        logger.info("Booster report written to %s", path)


if __name__ == "__main__":
    main()
