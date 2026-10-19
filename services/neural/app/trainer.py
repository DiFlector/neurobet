"""Model Trainer: Training, evaluation, artifact serialization, and PostgreSQL registry logging."""

import hashlib
import logging
import os
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import joblib
import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("neural.trainer")

from db.models import ModelVersion, TrainingRun, TrainingDataset
from .dataset import (
    MatchSnapshots,
    chronological_folds,
    decision_records,
    get_dataset_builder,
    positive_probabilities,
    stack_matches,
    thin_match,
)
from .metrics import compute_classification_metrics, error_summary, family_metrics, insufficient_metrics, sliced_metrics
from .models import (
    CALIBRATION_SCHEME,
    _holdout_cut,
    build_gradient_boosting_pipeline,
    build_logistic_regression_pipeline,
)
from .versioning import allocate_test_version, next_release_version


def _bump_for_calibration_change(
    session: Session,
    sport_code: str,
    model_name: str,
    market: str,
    bump: str,
) -> str:
    """A new calibration scheme is a minor release, not a patch."""
    if bump != "patch":
        return bump
    latest = (
        session.query(ModelVersion.hyperparameters)
        .filter(
            ModelVersion.sport_code == sport_code,
            ModelVersion.model_name == model_name,
            ModelVersion.market == market,
        )
        .order_by(ModelVersion.created_at.desc())
        .first()
    )
    previous = latest[0] if latest else None
    previous_scheme = previous.get("calibration_scheme") if isinstance(previous, dict) else None
    if previous_scheme == CALIBRATION_SCHEME:
        return bump
    logger.info(
        "Calibration scheme for %s changed from %s to %s. The version step is minor.",
        model_name,
        previous_scheme or "row_cv",
        CALIBRATION_SCHEME,
    )
    return "minor"


def _match_guard(
    session: Session,
    model_name: str,
    sport_code: str,
    market: str,
    by_family: Dict[str, Any],
) -> Dict[str, Any]:
    """Record whether the match-family walk-forward Brier stayed within 0.02 of the previous line."""
    current = (by_family.get("match") or {}).get("brier_score")
    previous = (
        session.query(ModelVersion)
        .filter(
            ModelVersion.sport_code == sport_code,
            ModelVersion.model_name == model_name,
            ModelVersion.market == market,
            ModelVersion.version_tag.like(f"{sport_code}_v%"),
        )
        .order_by(ModelVersion.created_at.desc())
        .first()
    )
    previous_brier = None
    previous_tag = None
    if previous is not None and isinstance(previous.metrics, dict):
        previous_tag = previous.version_tag
        raw = previous.metrics.get("brier_score")
        previous_brier = float(raw) if raw is not None else None
    degraded = (
        current is not None
        and previous_brier is not None
        and float(current) > previous_brier + 0.02
    )
    if degraded:
        logger.warning(
            "Match-family Brier %.4f is worse than %s %.4f by more than 0.02. The run stays SHADOW.",
            float(current),
            previous_tag,
            previous_brier,
        )
    return {
        "previous_version": previous_tag,
        "previous_brier": previous_brier,
        "current_brier": current,
        "degraded": bool(degraded),
    }


def _log_loss_gap(metrics_train: Dict[str, Any], metrics_val: Dict[str, Any]) -> Optional[float]:
    """How much the decision log loss sits above the training log loss. None when the check is empty."""
    if metrics_val.get("insufficient_validation"):
        return None
    train_loss = metrics_train.get("log_loss")
    val_loss = metrics_val.get("log_loss")
    if train_loss is None or val_loss is None:
        return None
    return round(float(val_loss) - float(train_loss), 4)


def training_lock_key(sport_code: str, model_name: str, market: str) -> int:
    """Stable signed 64-bit key for one sport, one model line, one market."""
    raw = f"neurobet-train:{sport_code}:{model_name}:{market}".encode()
    value = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=False)
    if value >= 2**63:
        value -= 2**64
    return value


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
    checkpoint serialization, and database registry tracking across all sports.
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

    def train_model(
        self,
        session: Session,
        model_type: str = "lr",  # "lr" or "gb"
        sport_code: str = "tennis",
        market: str = "match_winner",
        bump: str = "patch",
        version_tag: Optional[str] = None,
        hyperparameters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Trains, calibrates, evaluates, saves, and registers a single model for any sport."""
        os.makedirs(self.checkpoint_dir, exist_ok=True)
        started_at = datetime.now(timezone.utc)
        sport_code = sport_code.lower()
        model_name, estimator, hyperparameters = self._build_estimator(model_type, sport_code, hyperparameters)

        if model_type == "market_offset_gb" and sport_code != "tennis":
            raise ValueError("market_offset_gb is currently a tennis-only model.")
        if model_type == "market_offset_gb" and version_tag != "tennis_v4.0.0":
            raise ValueError("The first tennis market-offset release must use --version-tag tennis_v4.0.0.")

        lock_key = training_lock_key(sport_code, model_name, market)
        acquired = session.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": lock_key},
        ).scalar()
        if not acquired:
            logger.info(
                "Training already running for %s %s. This start does not create another version.",
                sport_code,
                model_name,
            )
            return {
                "skipped": True,
                "model_name": model_name,
                "sport_code": sport_code,
                "reason": "already_running",
            }

        try:
            if version_tag:
                existing = (
                    session.query(ModelVersion)
                    .filter(ModelVersion.sport_code == sport_code, ModelVersion.version_tag == version_tag)
                    .first()
                )
                if existing is not None:
                    return {
                        "skipped": True,
                        "reason": "version_tag_already_registered",
                        "model_name": existing.model_name,
                        "version_tag": existing.version_tag,
                        "model_version_id": str(existing.id),
                        "artifact_path": existing.artifact_path,
                    }
            prepared_matches = None
            prepared_names = None
            if model_type in ("epoch_lr", "epoch_gb", "epoch_mlp"):
                existing = (
                    session.query(ModelVersion)
                    .filter(
                        ModelVersion.sport_code == sport_code,
                        ModelVersion.model_name == model_name,
                        ModelVersion.market == market,
                        ModelVersion.version_tag == "tennis_v3.0.0",
                    )
                    .first()
                )
                if existing is not None:
                    return {
                        "skipped": True,
                        "reason": "already_registered",
                        "model_name": model_name,
                        "version_tag": "tennis_v3.0.0",
                        "model_version_id": str(existing.id),
                        "artifact_path": existing.artifact_path,
                    }
                prepared_matches, prepared_names, estimator, hyperparameters = self._prepare_epoch_line(
                    session, estimator, hyperparameters, sport_code
                )
                version_tag = "tennis_v3.0.0"
            return self._train_locked(
                session=session,
                estimator=estimator,
                model_name=model_name,
                model_type=model_type,
                sport_code=sport_code,
                market=market,
                bump=bump,
                version_tag=version_tag,
                hyperparameters=hyperparameters,
                started_at=started_at,
                prepared_matches=prepared_matches,
                prepared_names=prepared_names,
            )
        finally:
            try:
                session.rollback()
                session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
            except Exception:
                logger.exception("Could not release the training lock for %s", model_name)

    def _build_estimator(
        self,
        model_type: str,
        sport_code: str,
        overrides: Optional[Dict[str, Any]],
    ) -> Tuple[str, Any, Dict[str, Any]]:
        overrides = overrides or {}
        if model_type == "lr":
            model_name = f"{sport_code}_logistic_regression"
            hyperparameters = {
                "C": float(overrides.get("C", 1.0)),
                "max_iter": int(overrides.get("max_iter", 1000)),
                "calibration": str(overrides.get("calibration", "sigmoid")),
                "calibration_scheme": CALIBRATION_SCHEME,
                "random_state": self.random_seed,
            }
            estimator = build_logistic_regression_pipeline(
                C=hyperparameters["C"],
                random_state=self.random_seed,
                calibration_method=hyperparameters["calibration"],
            )
        elif model_type in ("gb", "gbdt"):
            model_name = f"{sport_code}_gradient_boosting"
            hyperparameters = {
                "learning_rate": float(overrides.get("learning_rate", 0.02)),
                "max_iter": int(overrides.get("max_iter", 100)),
                "max_depth": int(overrides.get("max_depth", 3)),
                "calibration": str(overrides.get("calibration", "sigmoid")),
                "calibration_scheme": CALIBRATION_SCHEME,
                "random_state": self.random_seed,
            }
            estimator = build_gradient_boosting_pipeline(
                learning_rate=hyperparameters["learning_rate"],
                max_iter=hyperparameters["max_iter"],
                max_depth=hyperparameters["max_depth"],
                random_state=self.random_seed,
                calibration_method=hyperparameters["calibration"],
            )
        elif model_type in ("residual_gb", "residual_gbdt"):
            from .models import build_residual_boosting_pipeline

            model_name = f"{sport_code}_residual_gradient_boosting"
            hyperparameters = {
                "learning_rate": float(overrides.get("learning_rate", 0.02)),
                "max_iter": int(overrides.get("max_iter", 100)),
                "max_depth": int(overrides.get("max_depth", 3)),
                "calibration": str(overrides.get("calibration", "sigmoid")),
                "calibration_scheme": CALIBRATION_SCHEME,
                "max_offset": float(overrides.get("max_offset", 0.25)),
                "random_state": self.random_seed,
            }
            estimator = build_residual_boosting_pipeline(
                learning_rate=hyperparameters["learning_rate"],
                max_iter=hyperparameters["max_iter"],
                max_depth=hyperparameters["max_depth"],
                random_state=self.random_seed,
                calibration_method=hyperparameters["calibration"],
                max_offset=hyperparameters["max_offset"],
            )
        elif model_type == "market_offset_gb":
            from .models import TennisMarketOffsetEstimator

            model_name = "tennis_market_offset_boosting"
            hyperparameters = {
                "objective": "binary:logistic",
                "base_margin": "logit(no_vig_prob_player_a)",
                "max_depth": int(overrides.get("max_depth", 3)),
                "learning_rate": float(overrides.get("learning_rate", 0.03)),
                "n_estimators": int(overrides.get("n_estimators", 300)),
                "reg_lambda": float(overrides.get("reg_lambda", 10.0)),
                "random_state": int(overrides.get("random_state", self.random_seed)),
                "n_jobs": int(overrides.get("n_jobs", 2)),
                "early_stopping_rounds": int(overrides.get("early_stopping_rounds", 30)),
                "market_offset_clip": 0.35,
                "alpha_grid": [0.0, 0.25, 0.5, 0.75, 1.0],
                "split": [0.6, 0.2, 0.2],
                "calibration": "temporal_alpha_grid_log_loss",
                "calibration_scheme": "market_offset_v1_temporal_60_20_20",
            }
            estimator = TennisMarketOffsetEstimator(
                max_depth=hyperparameters["max_depth"],
                learning_rate=hyperparameters["learning_rate"],
                n_estimators=hyperparameters["n_estimators"],
                reg_lambda=hyperparameters["reg_lambda"],
                random_state=hyperparameters["random_state"],
                n_jobs=hyperparameters["n_jobs"],
                early_stopping_rounds=hyperparameters["early_stopping_rounds"],
            )
        elif model_type in ("epoch_lr", "epoch_gb", "epoch_mlp"):
            from .epoch_fit import EpochTunedEstimator, ResidualEpochEstimator, initial_params

            family = {"epoch_lr": "lr", "epoch_gb": "gb", "epoch_mlp": "mlp"}[model_type]
            model_name = {
                "epoch_lr": f"{sport_code}_logistic_regression_epoch",
                "epoch_gb": f"{sport_code}_gradient_boosting_epoch",
                "epoch_mlp": f"{sport_code}_residual_mlp_epoch",
            }[model_type]
            params = dict(initial_params(family))
            params.update(overrides.get("params") or {})
            hyperparameters = {
                "family": family,
                "params": params,
                "calibration": "sigmoid",
                "calibration_scheme": CALIBRATION_SCHEME,
                "random_state": self.random_seed,
                "version_tag": "tennis_v3.0.0",
            }
            if family == "mlp":
                estimator = ResidualEpochEstimator(params=params)
            else:
                base = build_logistic_regression_pipeline().estimator if family == "lr" else build_gradient_boosting_pipeline().estimator
                estimator = EpochTunedEstimator(estimator=base, method="sigmoid", family=family, params=params)
        else:
            raise ValueError(f"Unknown model_type: {model_type}. Must be 'lr', 'gb', 'gbdt', 'residual_gb', 'market_offset_gb', 'epoch_lr', 'epoch_gb', or 'epoch_mlp'")
        return model_name, estimator, hyperparameters

    def _train_locked(
        self,
        session: Session,
        estimator: Any,
        model_name: str,
        model_type: str,
        sport_code: str,
        market: str,
        bump: str,
        version_tag: Optional[str],
        hyperparameters: Dict[str, Any],
        started_at: datetime,
        prepared_matches: Optional[List[Any]] = None,
        prepared_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        from sklearn.base import clone

        dataset_builder = get_dataset_builder(sport_code)
        builder_version = getattr(dataset_builder.feature_builder, "feature_version", None)
        if builder_version:
            self.feature_version = str(builder_version)
        if prepared_matches is None:
            matches, feature_names = dataset_builder.load_matches(session)
        else:
            matches, feature_names = prepared_matches, list(prepared_names or [])
        source_feature_version = str(
            getattr(dataset_builder, "loaded_source_feature_version", builder_version or self.feature_version)
        )
        if sport_code == "tennis" and model_type == "market_offset_gb":
            hyperparameters["dataset_source_feature_version"] = source_feature_version
            if source_feature_version != str(builder_version or self.feature_version):
                hyperparameters["dataset_feature_migrations"] = ["favorite_trailing:reconstructed_from_opening_match_market"]

        if sport_code == "tennis" and model_type == "market_offset_gb":
            from features.selection import SELECTED_TENNIS_FEATURES_V4

            missing = [name for name in SELECTED_TENNIS_FEATURES_V4 if name not in feature_names]
            if missing:
                raise ValueError(f"Dataset cannot train tennis v4; missing feature columns: {', '.join(missing)}")
            target_indices = [feature_names.index(name) for name in SELECTED_TENNIS_FEATURES_V4]
            feature_names = list(SELECTED_TENNIS_FEATURES_V4)
            matches = [
                MatchSnapshots(
                    event_id=m.event_id,
                    started_at=m.started_at,
                    label=m.label,
                    rows=np.asarray(m.rows, dtype=float)[:, target_indices].tolist() if len(m.rows) else [],
                    observed_at=m.observed_at,
                    row_labels=m.row_labels,
                    row_markets=m.row_markets,
                    row_odds_a=m.row_odds_a,
                    row_odds_b=m.row_odds_b,
                    result_available_at=m.result_available_at,
                )
                for m in matches
            ]
            logger.info("Tennis v4 uses the canonical %d-feature contract.", len(feature_names))
        elif sport_code == "tennis" and model_type in ("residual_gb", "residual_gbdt"):
            from features.selection import SELECTED_TENNIS_FEATURES_V2
            target_indices = [i for i, name in enumerate(feature_names) if name in SELECTED_TENNIS_FEATURES_V2]
            pruned_names = [feature_names[i] for i in target_indices]
            pruned_matches = []
            for m in matches:
                if len(m.rows):
                    pruned_rows = np.asarray(m.rows, dtype=float)[:, target_indices].tolist()
                else:
                    pruned_rows = []
                pruned_matches.append(
                    MatchSnapshots(
                        event_id=m.event_id,
                        started_at=m.started_at,
                        label=m.label,
                        rows=pruned_rows,
                        observed_at=m.observed_at,
                        row_labels=m.row_labels,
                        row_markets=m.row_markets,
                        row_odds_a=m.row_odds_a,
                        row_odds_b=m.row_odds_b,
                        result_available_at=m.result_available_at,
                    )
                )
            matches = pruned_matches
            feature_names = pruned_names
            logger.info("Pruned tennis features to %d columns (Variant B / Anti-Overfitting).", len(feature_names))

        mode, folds = chronological_folds(len(matches), embargo_matches=1 if model_type == "market_offset_gb" else 0)
        if model_type == "market_offset_gb":
            # Scheduled starts are not sufficient when two real matches overlap.
            # Keep only training matches whose last observed row predates the test tape.
            safe_folds = []
            for train_index, test_index in folds:
                first_test = min(matches[index].observed_at[0] for index in test_index if matches[index].observed_at)
                safe_train = [
                    index for index in train_index
                    if matches[index].observed_at and matches[index].observed_at[-1] < first_test
                ]
                if safe_train:
                    safe_folds.append((safe_train, test_index))
            folds = safe_folds
            if not folds:
                raise ValueError("Tennis v4 has no walk-forward folds after purging overlapping match timelines.")

        if hyperparameters.get("calibration") == "isotonic" and len(matches) < 1000:
            hyperparameters["calibration"] = "sigmoid"
            hyperparameters["calibration_note"] = "sigmoid_until_1000_matches"
            estimator.method = "sigmoid"
        # Each test match is scored by a model fit only on earlier matches.
        # The 12-point cap is a training-only budget. Decisions must be replayed
        # over the complete chronological tape with the service-side quotes intact.
        decision_log: List[Dict[str, Any]] = []
        skipped_v4_folds: List[Dict[str, Any]] = []
        for train_index, test_index in folds:
            train_matches = [thin_match(matches[index]) for index in train_index]
            test_matches = [matches[index] for index in test_index]
            X_fold, y_fold, _weights = stack_matches(train_matches)
            if len(X_fold) < 8 or len(set(y_fold.tolist())) < 2:
                logger.info("Skip a validation fold for %s: the earlier matches do not contain both classes.", sport_code)
                continue
            fold_model = clone(estimator)
            try:
                fold_model.fit_matches(train_matches, feature_names)
            except ValueError as exc:
                if model_type != "market_offset_gb":
                    raise
                skipped_v4_folds.append({"train_matches": len(train_matches), "reason": str(exc)})
                logger.info("Skip an unusable tennis v4 walk-forward fold: %s", exc)
                continue
            fold_records = decision_records(fold_model, test_matches, feature_names, sport_code)
            for record in fold_records:
                record["feature_version"] = self.feature_version
            decision_log.extend(fold_records)

        saved_train_index = folds[-1][0]
        saved_matches = [thin_match(matches[index]) for index in saved_train_index]
        X_all, y_all, _weights = stack_matches(saved_matches)
        if len(X_all) < 8 or len(set(y_all.tolist())) < 2:
            raise ValueError(
                f"На срезах с реальной линией для '{sport_code}' нельзя обучить оба класса "
                f"(строк {len(X_all)})."
            )
        estimator.fit_matches(saved_matches, feature_names)
        if model_type == "market_offset_gb":
            hyperparameters["market_heads"] = estimator.head_report_
            hyperparameters["walk_forward_skipped_folds"] = skipped_v4_folds
        epoch_report = getattr(estimator, "epoch_report_", None)
        if epoch_report:
            hyperparameters["final_epochs"] = epoch_report
        base_cut = _holdout_cut(
            len(saved_matches),
            float(getattr(estimator, "calibration_fraction", 0.2)),
        )
        X_train, y_train, _base_weights = stack_matches(saved_matches[:base_cut] or saved_matches)

        train_prob = positive_probabilities(estimator, X_train)
        metrics_train = compute_classification_metrics(y_train, train_prob)
        by_family = family_metrics(decision_log)
        match_guard = _match_guard(session, model_name, sport_code, market, by_family)
        calibration_report = getattr(estimator, "calibration_report_", {})
        hyperparameters["calibration_report"] = calibration_report
        hyperparameters["match_guard"] = match_guard
        hyperparameters["metrics_by_market_family"] = by_family
        match_decisions = [row for row in decision_log if row.get("market") == "match_winner"]
        headline = match_decisions or decision_log
        y_decision = [int(row["label"]) for row in headline]
        p_decision = [float(row["probability"]) for row in headline]
        if y_decision and len(set(y_decision)) > 1:
            metrics_val = compute_classification_metrics(
                np.asarray(y_decision, dtype=int),
                np.asarray(p_decision, dtype=float),
            )
            metrics_val["insufficient_validation"] = 0.0
            decision_rows = len(y_decision)
        else:
            metrics_val = insufficient_metrics()
            decision_rows = len(y_decision)

        os.makedirs(self.checkpoint_dir, exist_ok=True)
        dt_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dataset_name = f"{sport_code}_real_dataset_{dt_str}"
        storage_path = os.path.join(self.checkpoint_dir, f"{dataset_name}.npz")
        np.savez_compressed(
            storage_path,
            X_train=X_train,
            y_train=y_train,
            feature_names=np.array(feature_names),
            y_decision=np.asarray(y_decision, dtype=int),
            p_decision=np.asarray(p_decision, dtype=float),
            decision_market=np.asarray([row.get("market") or "" for row in headline]),
        )
        db_dataset = TrainingDataset(
            name=dataset_name,
            sport_code=sport_code,
            date_from=matches[0].started_at,
            date_to=matches[-1].started_at,
            row_count=int(len(X_train) + decision_rows),
            storage_path=storage_path,
        )
        session.add(db_dataset)
        session.flush()
        ds_meta = {
            "dataset_id": str(db_dataset.id),
            "name": dataset_name,
            "row_count": int(len(X_train) + len(decision_log)),
            "storage_path": storage_path,
            "match_count": len(matches),
            "snapshot_count": sum(len(match.rows) for match in matches),
            "validation": mode,
            "decision_rows": decision_rows,
            "metrics_are_decision_rows": decision_rows > 0,
            "train_samples": int(len(X_train)),
            "val_samples": decision_rows,
            "metrics_by_slice": sliced_metrics(decision_log),
            "error_analysis": error_summary(decision_log),
            "log_loss_gap": _log_loss_gap(metrics_train, metrics_val),
            "metrics_by_market_family": by_family,
            "calibration_report": calibration_report,
            "match_guard": match_guard,
            "headline_market": "match_winner" if match_decisions else "all_decisions",
            "feature_count": len(feature_names),
            "is_real_data": True,
            "context_from_history": True,
        }
        if model_type == "market_offset_gb":
            ds_meta["feature_contract"] = "tennis_market_v4"
            ds_meta["feature_count"] = len(feature_names)
            ds_meta["source_feature_version"] = source_feature_version

        # 5. Serialize model checkpoint. The tag is only the version of this line.
        if model_type in ("epoch_lr", "epoch_gb", "epoch_mlp"):
            m_tag = model_type
        elif model_type == "market_offset_gb":
            m_tag = "market_offset_gb"
        else:
            m_tag = "lr" if model_type == "lr" else "gb"
        if version_tag is None:
            existing_tags = [
                row[0]
                for row in session.query(ModelVersion.version_tag)
                .filter(
                    ModelVersion.sport_code == sport_code,
                    ModelVersion.model_name == model_name,
                    ModelVersion.market == market,
                )
                .all()
            ]
            set_rows = any(
                match.row_markets and any(str(market).startswith("set_winner:") for market in match.row_markets)
                for match in matches
            )
            already_v2 = any(str(tag).startswith(f"{sport_code}_v2.") for tag in existing_tags)
            if sport_code == "tennis" and bump not in ("major", "test") and set_rows and not already_v2:
                logger.info("First tennis model with set prices. The version step is major, tennis_v2.0.0.")
                bump = "major"
            if bump == "test":
                version_tag = allocate_test_version(existing_tags, sport_code)
            else:
                bump = _bump_for_calibration_change(session, sport_code, model_name, market, bump)
                version_tag = next_release_version(existing_tags, bump, sport_code)
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
        if model_type == "market_offset_gb":
            checkpoint_data.update({
                "feature_schema_version": "tennis_market_v4",
                "supported_markets": ["match_winner", "set_winner"],
                "market_heads": ["match", "set"],
            })
        joblib.dump(checkpoint_data, artifact_path)

        # Also save latest pointer
        latest_path = os.path.join(self.checkpoint_dir, f"{sport_code}_{market}_{m_tag}_latest.joblib")
        joblib.dump(checkpoint_data, latest_path)

        # 6. Database registry logging.
        # A fresh training run stays in shadow until promote/rollback replaces ACTIVE.
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
            status="SHADOW",
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

    def _prepare_epoch_line(self, session: Session, estimator: Any, hyperparameters: Dict[str, Any], sport_code: str):
        """Search hyperparameters on all but the latest 100 matches."""
        from .dataset import thin_match
        from .epoch_fit import evaluate_family, search_until_plateau
        from .models import _holdout_cut
        from .epoch_fit import split_holdout

        matches, names = get_dataset_builder(sport_code).load_matches(session)
        trainable, holdout = split_holdout(matches)
        family = str(hyperparameters["family"])
        hyperparameters["holdout_event_ids"] = [str(match.event_id) for match in holdout]
        hyperparameters["holdout_count"] = len(holdout)
        cut = _holdout_cut(len(trainable), 0.2)
        search_train = [thin_match(match) for match in trainable[:cut]]
        search_val = [thin_match(match) for match in trainable[cut:]]
        logger.info(
            "Epoch search %s uses %d matches and holds out %d. Validation matches: %d.",
            family,
            len(search_train),
            len(holdout),
            len(search_val),
        )

        def evaluate(params: Dict[str, Any], state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
            return evaluate_family(family, params, search_train, search_val, names, state)

        found = search_until_plateau(family, evaluate)
        params = dict(found["params"])
        hyperparameters["params"] = params
        hyperparameters["search"] = {
            "log_loss": found["log_loss"],
            "brier": found["brier"],
            "best_epoch": found.get("best_epoch"),
            "reason": found.get("reason"),
            "visited": found.get("visited"),
        }
        estimator.params = params
        return [thin_match(match) for match in trainable], names, estimator, hyperparameters

    def train_all(self, session: Session, sport_code: str = "tennis", bump: str = "patch") -> List[Dict[str, Any]]:
        """Trains and registers both baseline models: Logistic Regression & Gradient Boosting for a sport."""
        results = []
        for m_type in ["lr", "gb"]:
            res = self.train_model(session=session, model_type=m_type, sport_code=sport_code, bump=bump)
            results.append(res)
        return results

    def train_all_sports(self, session: Session, sports: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Trains and registers baseline models across all supported sports."""
        target_sports = sports or ["tennis", "football", "basketball", "hockey", "volleyball"]
        all_results = []
        for s in target_sports:
            results = self.train_all(session=session, sport_code=s)
            all_results.extend(results)
        return all_results
