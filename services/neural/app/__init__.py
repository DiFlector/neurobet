"""Neural ML package: training, baselines, metrics, and inference."""

from .inference import MLPredictor
from .metrics import compute_classification_metrics
from .models import build_gradient_boosting_pipeline, build_logistic_regression_pipeline
from .trainer import ModelTrainer

__all__ = [
    "MLPredictor",
    "ModelTrainer",
    "build_logistic_regression_pipeline",
    "build_gradient_boosting_pipeline",
    "compute_classification_metrics",
]
