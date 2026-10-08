"""Model Trainer: Training, evaluation, artifact serialization, and PostgreSQL registry logging."""

import os
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import joblib
from sqlalchemy.orm import Session

from db.models import ModelVersion, TrainingRun, TrainingDataset
from .dataset import TennisDatasetBuilder
from .metrics import compute_classification_metrics
from .models import build_gradient_boosting_pipeline, build_logistic_regression_pipeline


def get_git_commit_hash() -> str:
    """Retrieves current git commit hash if available, or fallback."""
    try:
        output = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
        return output.decode("utf-8").strip()
    except Exception:
        return os.getenv("GIT_COMMIT", "dev_head")


class ModelTrainer:
    """
    Orchestrates ML baseline training, calibration, metrics evaluation,
    checkpoint serialization, and database registry tracking.
    """

    def __init__(
        self,
        checkpoint_dir: str = "/app/models/checkpoints",
        feature_version: str = "1.0.0",
        random_seed: int = 42,
    ):
        self.checkpoint_dir = checkpoint_dir
        self.feature_version = feature_version
        self.random_seed = random_seed
        self.dataset_builder = TennisDatasetBuilder()

    def train_model(
        self,
        session: Session,
        model_type: str = "lr",  # "lr" or "gb"
        sport_code: str = "tennis",
        market: str = "match_winner",
    ) -> Dict[str, Any]:
        """Trains, calibrates, evaluates, saves, and registers a single model."""
        os.makedirs(self.checkpoint_dir, exist_ok=True)
        started_at = datetime.now(timezone.utc)

        # 1. Build dataset
        X_train, y_train, X_val, y_val, feature_names, ds_meta = self.dataset_builder.build_from_db(
            session=session,
            save_dir=self.checkpoint_dir,
        )

        # 2. Build model architecture & hyperparameters
        if model_type == "lr":
            model_name = "tennis_logistic_regression"
            hyperparameters = {
                "C": 1.0,
                "max_iter": 1000,
                "calibration": "sigmoid",
                "random_state": self.random_seed,
            }
            estimator = build_logistic_regression_pipeline(
                C=hyperparameters["C"],
                random_state=self.random_seed,
                calibration_method=hyperparameters["calibration"],
            )
        elif model_type == "gb":
            model_name = "tennis_gradient_boosting"
            hyperparameters = {
                "learning_rate": 0.05,
                "max_iter": 100,
                "max_depth": 4,
                "calibration": "isotonic",
                "random_state": self.random_seed,
            }
            estimator = build_gradient_boosting_pipeline(
                learning_rate=hyperparameters["learning_rate"],
                max_iter=hyperparameters["max_iter"],
                max_depth=hyperparameters["max_depth"],
                random_state=self.random_seed,
                calibration_method=hyperparameters["calibration"],
            )
        else:
            raise ValueError(f"Unknown model_type: {model_type}. Must be 'lr' or 'gb'")

        # 3. Train calibrated model
        estimator.fit(X_train, y_train)

        # 4. Evaluate metrics
        train_prob = estimator.predict_proba(X_train)[:, 1]
        val_prob = estimator.predict_proba(X_val)[:, 1]

        metrics_train = compute_classification_metrics(y_train, train_prob)
        metrics_val = compute_classification_metrics(y_val, val_prob)

        # 5. Serialize model checkpoint
        now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        version_tag = f"v1.0.0_{model_type}_{now_str}_{uuid.uuid4().hex[:4]}"
        artifact_filename = f"{sport_code}_{market}_{model_name}_{version_tag}.joblib"
        artifact_path = os.path.join(self.checkpoint_dir, artifact_filename)

        checkpoint_data = {
            "model": estimator,
            "feature_names": feature_names,
            "model_name": model_name,
            "version_tag": version_tag,
            "sport_code": sport_code,
            "market": market,
            "feature_version": self.feature_version,
            "metrics_val": metrics_val,
            "metrics_train": metrics_train,
            "hyperparameters": hyperparameters,
            "git_commit": get_git_commit_hash(),
            "trained_at": datetime.now(timezone.utc).isoformat(),
        }
        joblib.dump(checkpoint_data, artifact_path)

        # Also save latest pointer
        latest_path = os.path.join(self.checkpoint_dir, f"{sport_code}_{market}_{model_type}_latest.joblib")
        joblib.dump(checkpoint_data, latest_path)

        # 6. Database registry logging
        finished_at = datetime.now(timezone.utc)

        db_model_version = ModelVersion(
            model_name=model_name,
            version_tag=version_tag,
            sport_code=sport_code,
            market=market,
            artifact_path=artifact_path,
            metrics=metrics_val,
            hyperparameters={
                **hyperparameters,
                "feature_version": self.feature_version,
                "git_commit": get_git_commit_hash(),
                "dataset_name": ds_meta["name"],
            },
            status="ACTIVE",
        )
        session.add(db_model_version)
        session.flush()

        ds_uuid = None
        if ds_meta.get("dataset_id"):
            try:
                cand_id = uuid.UUID(ds_meta["dataset_id"])
                if session.query(TrainingDataset).filter(TrainingDataset.id == cand_id).first():
                    ds_uuid = cand_id
            except Exception:
                ds_uuid = None

        db_training_run = TrainingRun(
            model_version_id=db_model_version.id,
            dataset_id=ds_uuid,
            sport_code=sport_code,
            status="completed",
            metrics_train=metrics_train,
            metrics_val=metrics_val,
            started_at=started_at,
            finished_at=finished_at,
        )
        session.add(db_training_run)
        session.commit()

        return {
            "model_name": model_name,
            "version_tag": version_tag,
            "model_version_id": str(db_model_version.id),
            "training_run_id": str(db_training_run.id),
            "artifact_path": artifact_path,
            "metrics_val": metrics_val,
            "metrics_train": metrics_train,
            "dataset_meta": ds_meta,
            "hyperparameters": hyperparameters,
            "git_commit": get_git_commit_hash(),
            "feature_version": self.feature_version,
        }

    def train_all(self, session: Session, sport_code: str = "tennis") -> List[Dict[str, Any]]:
        """Trains and registers both baseline models: Logistic Regression & Gradient Boosting."""
        results = []
        for m_type in ["lr", "gb"]:
            res = self.train_model(session=session, model_type=m_type, sport_code=sport_code)
            results.append(res)
        return results
