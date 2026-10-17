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
    loss = float(log_loss(y_true_arr, y_prob_clipped, labels=[0, 1]))

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


def insufficient_metrics() -> Dict[str, float]:
    """Release metrics when no confirming decision exists. The tape is not a substitute."""
    return {
        "log_loss": 0.0,
        "brier_score": 0.0,
        "accuracy": 0.0,
        "roc_auc": 0.5,
        "expected_calibration_error": 0.0,
        "insufficient_validation": 1.0,
    }


def reliability_bins(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 5) -> list:
    """Mean predicted probability against the observed frequency in each bin."""
    truth = np.asarray(y_true, dtype=float)
    prob = np.asarray(y_prob, dtype=float)
    if len(truth) == 0:
        return []
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins = []
    for index in range(n_bins):
        mask = (prob >= edges[index]) & (prob < edges[index + 1] if index < n_bins - 1 else prob <= edges[index + 1])
        count = int(np.sum(mask))
        bins.append({
            "bin": f"{edges[index]:.1f}-{edges[index + 1]:.1f}",
            "count": count,
            "mean_probability": round(float(np.mean(prob[mask])), 4) if count else None,
            "observed_frequency": round(float(np.mean(truth[mask])), 4) if count else None,
        })
    return bins


CONFIDENT_MISS_PROBABILITY = 0.75
OVERFIT_LOG_LOSS_GAP = 0.02
BRIER_TOLERANCE = 0.02


def chosen_side_probability(probability: float) -> float:
    """Probability of the side the model names. A value of 0.5 stays on side A."""
    value = float(probability)
    return value if value >= 0.5 else 1.0 - value


def confidence_bucket(probability: float) -> str:
    """Bucket of the chosen side. The top bucket is a confident call."""
    chosen = chosen_side_probability(probability)
    if chosen < 0.60:
        return "0.50-0.60"
    if chosen < CONFIDENT_MISS_PROBABILITY:
        return "0.60-0.75"
    return "0.75+"


def side_is_correct(label: int, probability: float) -> bool:
    predicted = 1 if float(probability) >= 0.5 else 0
    return int(label) == predicted


def record_in_stake_window(record: Dict[str, Any]) -> bool:
    """Odds of the chosen side sit in the sport stake window. Tennis reads tennis.cfg."""
    if "in_bet_window" in record:
        return bool(record["in_bet_window"])
    from .strategy import stake_gate

    chosen = chosen_side_probability(record.get("probability") or 0.0)
    allowed, _reason = stake_gate(
        chosen,
        float(record.get("odds") or 0.0),
        str(record.get("market") or "match_winner"),
        str(record.get("sport_code") or "tennis"),
    )
    return bool(allowed)


def is_critical_miss(record: Dict[str, Any]) -> bool:
    """A confirming decision inside the stake window whose chosen side loses."""
    if "critical_miss" in record:
        return bool(record["critical_miss"])
    wrong = not side_is_correct(int(record.get("label") or 0), float(record.get("probability") or 0.0))
    return wrong and record_in_stake_window(record)


def is_confident_miss(record: Dict[str, Any]) -> bool:
    """The chosen side is at least 0.75 and the label disagrees."""
    if "confident_miss" in record:
        return bool(record["confident_miss"])
    probability = float(record.get("probability") or 0.0)
    wrong = not side_is_correct(int(record.get("label") or 0), probability)
    return wrong and chosen_side_probability(probability) >= CONFIDENT_MISS_PROBABILITY


def error_summary(records: list) -> Dict[str, Any]:
    """Counts of critical and confident misses on confirming decisions."""
    total = len(records)
    critical = [row for row in records if is_critical_miss(row)]
    confident = [row for row in records if is_confident_miss(row)]
    in_window = [row for row in records if record_in_stake_window(row)]
    critical_in_window = [row for row in critical if record_in_stake_window(row)]
    critical_confident = [
        row for row in critical
        if chosen_side_probability(row.get("probability") or 0.0) >= CONFIDENT_MISS_PROBABILITY
    ]

    def rate(count: int) -> float:
        return round(count / total, 4) if total else 0.0

    return {
        "decisions": total,
        "critical_misses": len(critical),
        "critical_miss_rate": rate(len(critical)),
        "confident_misses": len(confident),
        "confident_miss_rate": rate(len(confident)),
        "in_stake_window": len(in_window),
        "critical_misses_in_window": len(critical_in_window),
        "critical_misses_outside_window": len(critical) - len(critical_in_window),
        "critical_misses_confident": len(critical_confident),
    }


def loss_adjustment(log_loss_gap: float, summary: Dict[str, Any]) -> str:
    """Regularize when the check is worse than training. Otherwise upweight hard rows."""
    if float(log_loss_gap) > OVERFIT_LOG_LOSS_GAP:
        return "regularize"
    critical = int(summary.get("critical_misses") or 0)
    if critical <= 0:
        return "none"
    in_window = int(summary.get("critical_misses_in_window") or 0) / critical
    confident = int(summary.get("critical_misses_confident") or 0) / critical
    if in_window >= 0.5 or confident >= 0.5:
        return "gamma"
    return "none"


def copy_beats_baseline(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    """The copy must miss less often, and its Brier may not fall behind by more than 0.02."""
    if float(candidate.get("critical_miss_rate") or 0.0) >= float(baseline.get("critical_miss_rate") or 0.0):
        return False
    if float(candidate.get("brier_score") or 0.0) > float(baseline.get("brier_score") or 0.0) + BRIER_TOLERANCE:
        return False
    return True


def _odds_bucket(odds: float) -> str:
    if odds < 1.2:
        return "below_1.20"
    if odds < 1.8:
        return "1.20-1.80"
    if odds < 2.5:
        return "1.80-2.50"
    if odds <= 4.0:
        return "2.50-4.00"
    return "above_4.00"


def market_family(market: str) -> str:
    """Match and every set share one family each. Individual set numbers stay in the market slice."""
    return "set" if str(market or "").startswith("set_winner") else "match"


def family_metrics(records: list) -> Dict[str, Any]:
    """Walk-forward metrics for the match family and the set family."""
    grouped: Dict[str, list] = {"match": [], "set": []}
    for record in records:
        family = str(record.get("market_family") or "")
        if family not in grouped:
            family = market_family(str(record.get("market") or ""))
        grouped[family].append(record)
    report = {}
    for family, rows in grouped.items():
        labels = np.asarray([row["label"] for row in rows], dtype=int)
        probs = np.asarray([row["probability"] for row in rows], dtype=float) if rows else np.empty((0,))
        if len(rows) == 0 or len(set(labels.tolist())) < 2:
            metrics = {"count": int(len(rows)), "insufficient": 1.0}
        else:
            metrics = compute_classification_metrics(labels, probs)
            metrics["count"] = int(len(rows))
        metrics["reliability"] = reliability_bins(labels, probs)
        report[family] = metrics
    return report


def sliced_metrics(records: list) -> Dict[str, Any]:
    """AUC, log loss, Brier, ECE and reliability split by market, odds and phase."""
    groups: Dict[str, list] = {}
    for record in records:
        keys = [
            f"market:{record.get('market') or 'match_winner'}",
            f"family:{record.get('market_family') or market_family(str(record.get('market') or ''))}",
            f"odds:{_odds_bucket(float(record.get('odds') or 0.0))}",
            f"phase:{record.get('phase') or 'unknown'}",
        ]
        if record.get("feature_version"):
            keys.append(f"feature:{record['feature_version']}")
        keys.append(f"confidence:{confidence_bucket(float(record.get('probability') or 0.0))}")
        keys.append("window:in_stake_window" if record_in_stake_window(record) else "window:outside_stake_window")
        for key in keys:
            groups.setdefault(key, []).append(record)
    report = {}
    for key, rows in groups.items():
        labels = np.asarray([row["label"] for row in rows], dtype=int)
        probs = np.asarray([row["probability"] for row in rows], dtype=float)
        if len(set(labels.tolist())) < 2:
            metrics = {"count": int(len(rows)), "insufficient": 1.0}
        else:
            metrics = compute_classification_metrics(labels, probs)
            metrics["count"] = int(len(rows))
        metrics["reliability"] = reliability_bins(labels, probs)
        report[key] = metrics
    return report
