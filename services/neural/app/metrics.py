"""ML evaluation metrics: Log Loss, Brier Score, ROC AUC, Accuracy, and Calibration Error."""

from typing import Any, Dict
import numpy as np
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score


def calculate_expected_calibration_error(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> float:
    """
    Computes Expected Calibration Error (ECE) across n_bins.
    ECE = sum_{b} (|B_b| / N) * |acc(B_b) - conf(B_b)|
    """
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    bin_indices = np.digitize(y_prob, bins) - 1
    ece = 0.0
    n_samples = len(y_true)

    for b in range(n_bins):
        mask = bin_indices == b
        bin_count = np.sum(mask)
        if bin_count > 0:
            bin_acc = np.mean(y_true[mask])
            bin_conf = np.mean(y_prob[mask])
            ece += (bin_count / n_samples) * abs(bin_acc - bin_conf)

    return round(float(ece), 6)


def compute_classification_metrics(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    threshold: float = 0.5,
) -> Dict[str, float]:
    """
    Computes complete set of probabilistic and classification metrics:
    Log Loss, Brier Score, ROC AUC, Accuracy, and ECE.
    """
    y_true_arr = np.asarray(y_true, dtype=int)
    y_prob_arr = np.asarray(y_prob, dtype=float)

    # Clip probabilities for stable log_loss calculation
    eps = 1e-15
    y_prob_clipped = np.clip(y_prob_arr, eps, 1.0 - eps)

    # Brier Score
    brier = float(brier_score_loss(y_true_arr, y_prob_arr))

    # Log Loss
    loss = float(log_loss(y_true_arr, y_prob_clipped))

    # Accuracy
    y_pred = (y_prob_arr >= threshold).astype(int)
    acc = float(accuracy_score(y_true_arr, y_pred))

    # ROC AUC (if at least 2 distinct classes present)
    if len(np.unique(y_true_arr)) > 1:
        auc = float(roc_auc_score(y_true_arr, y_prob_arr))
    else:
        auc = 0.5

    # Calibration metric
    ece = calculate_expected_calibration_error(y_true_arr, y_prob_arr)

    return {
        "log_loss": round(loss, 4),
        "brier_score": round(brier, 4),
        "accuracy": round(acc, 4),
        "roc_auc": round(auc, 4),
        "expected_calibration_error": round(ece, 4),
    }
