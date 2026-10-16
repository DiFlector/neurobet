"""Fit the pinned composite bundle and register it as one SHADOW line.

The frozen comparison blocks stay out of the fit. This does not promote a model
and does not retire the existing shadow lines.
"""

import logging
import os

from db.connection import SessionLocal
from db.models import ExperimentResult, ModelVersion

from app.compare.feature_models import (
    BoostingFeatureModel,
    LogisticFeatureModel,
    ResidualMLPFeatureModel,
    prepare,
)
from app.composite_predictor import bundle_metadata
from app.composite_vote import COMPONENT_LABELS, DISPLAY_LABEL
from app.dataset import get_dataset_builder
from app.metrics import compute_classification_metrics
from app.versioning import next_release_version

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("neural.composite")

CHECKPOINT_DIR = os.getenv("NEUROBET_CHECKPOINT_DIR", "/app/models/checkpoints")
MODEL_NAME = "tennis_composite"


def frozen_test_ids(session) -> set:
    """Event ids already used as reported holdouts. They are not training rows."""
    names = ("tennis_hypothesis_holdout%", "tennis_feature103_holdout%")
    blocked = set()
    for name in names:
        rows = session.query(ExperimentResult).filter(ExperimentResult.name.like(name)).all()
        for row in rows:
            payload = row.strategy_config or {}
            blocked.update(str(item) for item in payload.get("test_event_ids") or [])
    return blocked


def main() -> None:
    import joblib

    with SessionLocal() as session:
        blocked = frozen_test_ids(session)
        matches, names = get_dataset_builder("tennis").load_matches(session)
        prepared = [
            match
            for match in prepare(matches)
            if str(match.event_id) not in blocked
        ]
        prepared.sort(key=lambda match: (match.started_at, str(match.event_id)))
        if len(prepared) < 20:
            raise SystemExit("Слишком мало матчей для составной модели.")
        cut = max(1, int(len(prepared) * 0.8))
        if cut >= len(prepared):
            cut = len(prepared) - 1
        train, validation = prepared[:cut], prepared[cut:]
        logger.info("Composite fit train=%s validation=%s excluded=%s", len(train), len(validation), len(blocked))

        logistic = LogisticFeatureModel()
        logistic.chosen = {"C": 0.01}
        logistic.fit_final(train, validation, names)
        boosting = BoostingFeatureModel()
        boosting.chosen = {"learning_rate": 0.1, "max_depth": 3, "min_samples_leaf": 20}
        boosting.fit_final(train, validation, names)
        residual = ResidualMLPFeatureModel(max_epochs=25)
        residual.configs = ({"widths": (64, 32), "dropout": 0.1, "weight_decay": 1e-3},)
        residual.select(train, validation, names)
        residual.fit_final(train, validation, names)

        existing = [
            row.version_tag
            for row in session.query(ModelVersion).filter(ModelVersion.model_name == MODEL_NAME).all()
        ]
        version = next_release_version(existing, "minor" if existing else "patch", "tennis")
        network = residual.bundle["model"]
        network.cpu()
        payload = {
            "kind": "composite",
            "model_name": MODEL_NAME,
            "version_tag": version,
            "market": "match_winner",
            "feature_names": list(names),
            "display_label": DISPLAY_LABEL,
            "components": {
                "logistic": {
                    "label": COMPONENT_LABELS["logistic"],
                    "version": version,
                    "model": logistic.model,
                    "hyperparameters": {"C": 0.01, "calibration": "sigmoid"},
                },
                "boosting": {
                    "label": COMPONENT_LABELS["boosting"],
                    "version": version,
                    "model": boosting.model,
                    "hyperparameters": boosting.chosen,
                },
                "residual_mlp": {
                    "label": COMPONENT_LABELS["residual_mlp"],
                    "version": version,
                    "widths": [64, 32],
                    "dropout": 0.1,
                    "weight_decay": 1e-3,
                    "state_dict": {key: value.detach().cpu() for key, value in network.state_dict().items()},
                    "scaler_mean": residual.bundle["scaler"].mean_.astype(float).tolist(),
                    "scaler_scale": residual.bundle["scaler"].scale_.astype(float).tolist(),
                    "calibrators": residual._calibrators,
                    "epochs": residual.bundle["epochs"],
                },
            },
        }
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        path = os.path.join(CHECKPOINT_DIR, f"tennis_match_winner_composite_{version}.joblib")
        joblib.dump(payload, path)
        metrics = _validation_metrics(logistic, boosting, residual, validation, names)
        hyperparameters = bundle_metadata()
        hyperparameters["feature_count"] = len(names)
        hyperparameters["train_matches"] = len(train)
        hyperparameters["validation_matches"] = len(validation)
        hyperparameters["excluded_test_matches"] = len(blocked)
        row = ModelVersion(
            model_name=MODEL_NAME,
            version_tag=version,
            sport_code="tennis",
            market="match_winner",
            artifact_path=path,
            metrics=metrics,
            hyperparameters=hyperparameters,
            status="SHADOW",
        )
        session.add(row)
        session.commit()
        logger.info("Registered composite SHADOW %s at %s", version, path)
        print({"version_tag": version, "artifact": path, "metrics": metrics, "matches": len(prepared)})


def _validation_metrics(logistic, boosting, residual, validation, names) -> dict:
    from app.compare.feature_models import stack

    _rows, labels, _weights, _markets, _odds = stack(validation, names)
    summary = {}
    for name, model in (("logistic", logistic), ("boosting", boosting), ("residual_mlp", residual)):
        probabilities = model.predict(validation, names)
        metrics = compute_classification_metrics(labels, probabilities)
        summary[name] = {key: round(float(value), 4) for key, value in metrics.items()}
    summary["brier_score"] = summary["residual_mlp"].get("brier_score")
    summary["roc_auc"] = summary["residual_mlp"].get("roc_auc")
    return summary


if __name__ == "__main__":
    main()
