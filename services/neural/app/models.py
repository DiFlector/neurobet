"""Model pipelines: Logistic Regression and Gradient Boosting with probability calibration."""

from typing import Any
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def build_logistic_regression_pipeline(
    C: float = 1.0,
    random_state: int = 42,
    calibration_method: str = "sigmoid",
) -> CalibratedClassifierCV:
    """
    Constructs a calibrated Logistic Regression baseline.
    Includes StandardScaler to handle varying magnitudes of features and rolling window deltas.
    Calibrated with Platt scaling (sigmoid).
    """
    base_pipeline = Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    C=C,
                    max_iter=1000,
                    random_state=random_state,
                    solver="lbfgs",
                ),
            ),
        ]
    )

    calibrated_model = CalibratedClassifierCV(
        estimator=base_pipeline,
        method=calibration_method,
        cv=3,
    )
    return calibrated_model


def build_gradient_boosting_pipeline(
    learning_rate: float = 0.05,
    max_iter: int = 100,
    max_depth: int = 4,
    random_state: int = 42,
    calibration_method: str = "isotonic",
) -> CalibratedClassifierCV:
    """
    Constructs a calibrated Gradient Boosting baseline.
    Uses HistGradientBoostingClassifier (fast, robust, native NaN support)
    calibrated with isotonic regression.
    """
    base_model = HistGradientBoostingClassifier(
        learning_rate=learning_rate,
        max_iter=max_iter,
        max_depth=max_depth,
        random_state=random_state,
        min_samples_leaf=5,
    )

    calibrated_model = CalibratedClassifierCV(
        estimator=base_model,
        method=calibration_method,
        cv=3,
    )
    return calibrated_model
