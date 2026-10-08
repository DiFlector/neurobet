"""Comprehensive test suite for Phase 9: Baseline ML models, calibration, and inference."""

import os
import sys
import uuid
import numpy as np

# Configure sys.path
paths = [
    "/srv/neurobet/packages/contracts/src",
    "/srv/neurobet/packages/db/src",
    "/srv/neurobet/packages/features/src",
    "/srv/neurobet/services/neural",
    "/app/packages/contracts/src",
    "/app/packages/db/src",
    "/app/packages/features/src",
    "/app",
]
for p in paths:
    if p not in sys.path:
        sys.path.insert(0, p)

from contracts import FeatureVector, MLPrediction
from db.connection import SessionLocal
from db.models import ModelVersion, TrainingRun
from app.dataset import TennisDatasetBuilder
from app.inference import MLPredictor
from app.metrics import calculate_expected_calibration_error, compute_classification_metrics
from app.models import build_gradient_boosting_pipeline, build_logistic_regression_pipeline
from app.trainer import ModelTrainer


def test_metrics_calculation():
    """Verify Log Loss, Brier Score, ROC AUC, Accuracy, and ECE calculations."""
    y_true = np.array([1, 0, 1, 1, 0, 0, 1, 0])
    y_prob = np.array([0.85, 0.20, 0.70, 0.65, 0.15, 0.40, 0.90, 0.10])

    metrics = compute_classification_metrics(y_true, y_prob)

    assert "log_loss" in metrics
    assert "brier_score" in metrics
    assert "accuracy" in metrics
    assert "roc_auc" in metrics
    assert "expected_calibration_error" in metrics

    assert 0.0 <= metrics["log_loss"] <= 1.0
    assert 0.0 <= metrics["brier_score"] <= 0.25
    assert metrics["accuracy"] >= 0.75
    assert metrics["roc_auc"] >= 0.80
    assert 0.0 <= metrics["expected_calibration_error"] <= 0.5


def test_models_construction_and_calibration():
    """Verify Logistic Regression and Gradient Boosting model creation and calibration."""
    lr = build_logistic_regression_pipeline(C=1.0, calibration_method="sigmoid")
    assert lr is not None
    assert lr.method == "sigmoid"

    gb = build_gradient_boosting_pipeline(learning_rate=0.05, calibration_method="isotonic")
    assert gb is not None
    assert gb.method == "isotonic"

    # Quick fit test on toy data
    X_toy = np.random.randn(30, 5)
    y_toy = np.random.randint(0, 2, size=30)

    lr.fit(X_toy, y_toy)
    probs_lr = lr.predict_proba(X_toy)
    assert probs_lr.shape == (30, 2)
    assert np.allclose(probs_lr.sum(axis=1), 1.0)

    gb.fit(X_toy, y_toy)
    probs_gb = gb.predict_proba(X_toy)
    assert probs_gb.shape == (30, 2)
    assert np.allclose(probs_gb.sum(axis=1), 1.0)


def test_dataset_builder_point_in_time():
    """Verify dataset builder returns properly structured train and validation splits."""
    ds_builder = TennisDatasetBuilder()
    X_train, y_train, X_val, y_val, feature_names, meta = ds_builder.generate_synthetic_dataset(
        n_matches=30,
        train_ratio=0.7,
        random_seed=42,
    )

    assert len(X_train) > 0
    assert len(X_val) > 0
    assert len(y_train) == len(X_train)
    assert len(y_val) == len(X_val)
    assert len(feature_names) >= 10
    assert "current_odds_player_a" in feature_names
    assert "no_vig_prob_player_a" in feature_names
    assert "games_diff" in feature_names


def test_training_and_artifact_serialization():
    """Verify ModelTrainer trains models, saves checkpoints, and records in database."""
    test_chk_dir = "/tmp/test_neurobet_checkpoints"
    os.makedirs(test_chk_dir, exist_ok=True)

    trainer = ModelTrainer(checkpoint_dir=test_chk_dir, random_seed=42)

    with SessionLocal() as session:
        # Train Logistic Regression
        res_lr = trainer.train_model(session=session, model_type="lr", sport_code="tennis")
        assert res_lr["model_name"] == "tennis_logistic_regression"
        assert os.path.exists(res_lr["artifact_path"])
        assert res_lr["metrics_val"]["log_loss"] > 0.0

        # Train Gradient Boosting
        res_gb = trainer.train_model(session=session, model_type="gb", sport_code="tennis")
        assert res_gb["model_name"] == "tennis_gradient_boosting"
        assert os.path.exists(res_gb["artifact_path"])

        # Verify DB records
        mv_id = uuid.UUID(res_lr["model_version_id"])
        mv_record = session.query(ModelVersion).filter(ModelVersion.id == mv_id).first()
        assert mv_record is not None
        assert mv_record.status == "ACTIVE"
        assert "log_loss" in mv_record.metrics

        tr_id = uuid.UUID(res_lr["training_run_id"])
        tr_record = session.query(TrainingRun).filter(TrainingRun.id == tr_id).first()
        assert tr_record is not None
        assert tr_record.status == "completed"

        # Test inference with saved checkpoint
        predictor = MLPredictor(checkpoint_path=res_lr["artifact_path"])
        assert predictor.checkpoint_data is not None

        # Build dummy feature vector matching feature names
        dummy_features = {k: 0.0 for k in res_lr["feature_version"] and trainer.dataset_builder.feature_builder.POINT_MAPPING}
        for name in predictor.checkpoint_data["feature_names"]:
            dummy_features[name] = 1.85 if "odds" in name else 0.5

        f_vec = FeatureVector(
            event_id="test_ev_ml_1",
            sport_code="tennis",
            feature_version="1.0.0",
            features=dummy_features,
        )

        pred = predictor.predict(f_vec, odds_a=1.90, odds_b=1.90)
        assert isinstance(pred, MLPrediction)
        assert pred.event_id == "test_ev_ml_1"
        assert pred.model_version == res_lr["version_tag"]
        assert len(pred.outcomes) == 2

        out_a = next(o for o in pred.outcomes if o.outcome == "player_a")
        out_b = next(o for o in pred.outcomes if o.outcome == "player_b")

        assert round(out_a.model_probability + out_b.model_probability, 2) == 1.00
        assert out_a.fair_odds > 1.0
        assert out_b.fair_odds > 1.0
        assert out_a.edge == round((out_a.model_probability * out_a.bookmaker_odds) - 1.0, 4)


def test_training_reproducibility():
    """Verify that training with a fixed seed produces identical validation metrics."""
    test_chk_dir = "/tmp/test_reproducibility"
    os.makedirs(test_chk_dir, exist_ok=True)

    trainer1 = ModelTrainer(checkpoint_dir=test_chk_dir, random_seed=42)
    trainer2 = ModelTrainer(checkpoint_dir=test_chk_dir, random_seed=42)

    with SessionLocal() as session:
        res1 = trainer1.train_model(session=session, model_type="lr")
        res2 = trainer2.train_model(session=session, model_type="lr")

        assert res1["metrics_val"]["log_loss"] == res2["metrics_val"]["log_loss"]
        assert res1["metrics_val"]["brier_score"] == res2["metrics_val"]["brier_score"]


if __name__ == "__main__":
    test_metrics_calculation()
    print("test_metrics_calculation: OK")
    test_models_construction_and_calibration()
    print("test_models_construction_and_calibration: OK")
    test_dataset_builder_point_in_time()
    print("test_dataset_builder_point_in_time: OK")
    test_training_and_artifact_serialization()
    print("test_training_and_artifact_serialization: OK")
    test_training_reproducibility()
    print("test_training_reproducibility: OK")
    print("ALL BASELINE ML TESTS PASSED!")
