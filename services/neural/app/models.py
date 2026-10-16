"""Model pipelines: Logistic Regression and Gradient Boosting.

Calibration is fit on a later slice of matches, never on random rows of the
same match. `CalibratedClassifierCV(cv=3)` mixed snapshots of one match
between the base fit and the calibrator.
"""

from typing import Any, Optional, Sequence

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin, clone
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .dataset import project_market, stack_matches

CALIBRATION_SCHEME = "temporal_holdout"
DEFAULT_CALIBRATION_FRACTION = 0.2
MIN_ISOTONIC_MATCHES = 1000


def build_logistic_regression_estimator(
    C: float = 1.0,
    random_state: int = 42,
) -> Pipeline:
    """Uncalibrated logistic regression. Numeric features are scaled."""
    return Pipeline(
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


def build_gradient_boosting_estimator(
    learning_rate: float = 0.05,
    max_iter: int = 100,
    max_depth: int = 4,
    random_state: int = 42,
    max_leaf_nodes: Optional[int] = None,
    min_samples_leaf: int = 5,
    l2_regularization: float = 0.0,
) -> HistGradientBoostingClassifier:
    """Uncalibrated histogram gradient boosting for numeric features."""
    return HistGradientBoostingClassifier(
        learning_rate=learning_rate,
        max_iter=max_iter,
        max_depth=max_depth,
        max_leaf_nodes=max_leaf_nodes,
        min_samples_leaf=min_samples_leaf,
        l2_regularization=l2_regularization,
        random_state=random_state,
    )


def build_logistic_regression_pipeline(
    C: float = 1.0,
    random_state: int = 42,
    calibration_method: str = "sigmoid",
    calibration_fraction: float = DEFAULT_CALIBRATION_FRACTION,
) -> "TemporalCalibratedEstimator":
    """Logistic regression whose calibrator sees only the later matches."""
    return TemporalCalibratedEstimator(
        estimator=build_logistic_regression_estimator(C=C, random_state=random_state),
        method=calibration_method,
        calibration_fraction=calibration_fraction,
    )


def build_gradient_boosting_pipeline(
    learning_rate: float = 0.05,
    max_iter: int = 100,
    max_depth: int = 4,
    random_state: int = 42,
    calibration_method: str = "isotonic",
    calibration_fraction: float = DEFAULT_CALIBRATION_FRACTION,
    max_leaf_nodes: Optional[int] = None,
    min_samples_leaf: int = 5,
    l2_regularization: float = 0.0,
) -> "TemporalCalibratedEstimator":
    """Gradient boosting whose calibrator sees only the later matches."""
    return TemporalCalibratedEstimator(
        estimator=build_gradient_boosting_estimator(
            learning_rate=learning_rate,
            max_iter=max_iter,
            max_depth=max_depth,
            random_state=random_state,
            max_leaf_nodes=max_leaf_nodes,
            min_samples_leaf=min_samples_leaf,
            l2_regularization=l2_regularization,
        ),
        method=calibration_method,
        calibration_fraction=calibration_fraction,
    )


def fit_estimator(estimator: Any, rows: np.ndarray, labels: np.ndarray, weights: Optional[np.ndarray]) -> Any:
    """Fit a pipeline or a bare classifier. Row weights are optional."""
    if weights is None:
        estimator.fit(rows, labels)
        return estimator
    if isinstance(estimator, Pipeline):
        estimator.fit(rows, labels, classifier__sample_weight=weights)
        return estimator
    estimator.fit(rows, labels, sample_weight=weights)
    return estimator


def _both_classes(labels: np.ndarray) -> bool:
    return len(set(int(item) for item in labels.tolist())) >= 2


class TemporalCalibratedEstimator(ClassifierMixin, BaseEstimator):
    """Base model on earlier rows, calibrator on the later holdout.

    `fit` splits a matrix that is already ordered in time. `fit_matches`
    splits whole matches, so every snapshot of one match stays on one side.
    """

    def __init__(
        self,
        estimator: Any = None,
        method: str = "sigmoid",
        calibration_fraction: float = DEFAULT_CALIBRATION_FRACTION,
    ):
        self.estimator = estimator
        self.method = method
        self.calibration_fraction = calibration_fraction

    def fit(self, X, y, sample_weight=None):
        rows = np.asarray(X, dtype=float)
        labels = np.asarray(y, dtype=int)
        weights = None if sample_weight is None else np.asarray(sample_weight, dtype=float)
        cut = _holdout_cut(len(rows), self.calibration_fraction)
        self._fit_split(rows[:cut], labels[:cut], None if weights is None else weights[:cut],
                        rows[cut:], labels[cut:], None if weights is None else weights[cut:])
        return self

    def fit_matches(self, matches: Sequence[Any], feature_names: Optional[Sequence[str]] = None):
        """Fit on whole matches. The last fraction is the calibration holdout.

        When rows name their market, the base model still sees every earlier row,
        but match and set each get a calibrator fit only on that family's later rows.
        """
        ordered = [match for match in matches if len(match.rows) > 0]
        names = list(feature_names or [])
        self.market_feature_index_ = names.index("market_is_set") if "market_is_set" in names else None
        self.base_event_ids_ = []
        self.calibration_event_ids_ = []
        if len(ordered) < 2:
            rows, labels, weights = stack_matches(ordered)
            self._fit_split(rows, labels, weights, rows[:0], labels[:0], weights[:0] if len(weights) else weights)
            return self
        cut = _holdout_cut(len(ordered), self.calibration_fraction)
        earlier, later = ordered[:cut], ordered[cut:]
        self.base_event_ids_ = [str(match.event_id) for match in earlier]
        self.calibration_event_ids_ = [str(match.event_id) for match in later]
        base_rows, base_labels, base_weights = stack_matches(earlier)
        has_markets = self.market_feature_index_ is not None and any(match.row_markets for match in ordered)
        if not has_markets:
            calibration_matches = [project_market(match, "match") for match in later]
            calibration_matches = [match for match in calibration_matches if match.rows] or list(later)
            cal_rows, cal_labels, cal_weights = stack_matches(calibration_matches)
            self._fit_split(base_rows, base_labels, base_weights, cal_rows, cal_labels, cal_weights)
            return self
        self._fit_market_calibrators(base_rows, base_labels, base_weights, later)
        return self

    def predict_proba(self, X):
        return self.model_.predict_proba(X)

    def predict(self, X):
        return self.model_.predict(X)

    @property
    def classes_(self):
        return self.model_.classes_

    def _fit_split(self, base_rows, base_labels, base_weights, cal_rows, cal_labels, cal_weights) -> None:
        self.calibrated_ = False
        self.calibration_rows_ = int(len(cal_rows))
        base = clone(self.estimator)
        method = self.method
        if method == "isotonic" and len(cal_rows) < MIN_ISOTONIC_MATCHES:
            method = "sigmoid"
        use_calibration = (
            method not in ("none", "", None)
            and len(cal_rows) >= 8
            and len(base_rows) >= 8
            and _both_classes(base_labels)
            and _both_classes(cal_labels)
        )
        if not use_calibration:
            all_rows = base_rows if len(cal_rows) == 0 else np.vstack([base_rows, cal_rows])
            all_labels = base_labels if len(cal_labels) == 0 else np.concatenate([base_labels, cal_labels])
            if base_weights is None or cal_weights is None or len(cal_rows) == 0:
                all_weights = base_weights
            else:
                all_weights = np.concatenate([base_weights, cal_weights])
            self.model_ = fit_estimator(base, all_rows, all_labels, all_weights)
            self.calibration_report_ = {
                "match": {"method": None, "rows": int(len(cal_rows)), "reason": "calibration_skipped"},
                "set": {"method": None, "rows": 0, "reason": "no_market_split"},
            }
            return
        fitted = fit_estimator(base, base_rows, base_labels, base_weights)
        self.model_ = _HoldoutCalibrator(fitted, method)
        self.applied_calibration_ = method
        self.model_.fit_holdout(cal_rows, cal_labels, cal_weights)
        self.calibrated_ = True
        self.calibration_report_ = {
            "match": {"method": method, "rows": int(len(cal_rows)), "reason": ""},
            "set": {"method": None, "rows": 0, "reason": "no_market_split"},
        }

    def _fit_market_calibrators(self, base_rows, base_labels, base_weights, later) -> None:
        """One base model. Each market family is calibrated only on its own later rows."""
        fitted = fit_estimator(clone(self.estimator), base_rows, base_labels, base_weights)
        match_rows, match_labels, match_weights = stack_matches(
            [match for match in (project_market(item, "match") for item in later) if match.rows]
        )
        set_rows, set_labels, set_weights = stack_matches(
            [match for match in (project_market(item, "set") for item in later) if match.rows]
        )
        match_report, match_calibrator = self._fit_family(fitted, match_rows, match_labels, match_weights)
        set_report, set_calibrator = self._fit_family(fitted, set_rows, set_labels, set_weights)
        if set_calibrator is None and match_calibrator is not None:
            set_report["reason"] = set_report["reason"] or "fallback_to_match"
            set_report["fallback"] = "match"
        self.calibration_report_ = {"match": match_report, "set": set_report}
        self.model_ = _MarketAwareCalibrator(
            fitted,
            match_calibrator,
            set_calibrator,
            int(self.market_feature_index_),
        )
        self.applied_calibration_ = "market_split"
        self.calibrated_ = match_calibrator is not None or set_calibrator is not None
        self.calibration_rows_ = int(len(match_rows) + len(set_rows))

    def _fit_family(self, fitted_base, rows, labels, weights):
        report = {"method": None, "rows": int(len(rows)), "reason": "", "label_mean": None}
        if len(rows) == 0 or not _both_classes(np.asarray(labels)):
            report["reason"] = "insufficient_rows"
            return report, None
        if len(rows) < 8:
            report["reason"] = "insufficient_rows"
            return report, None
        report["label_mean"] = round(float(np.mean(labels)), 6)
        method = self.method
        if method == "isotonic" and len(rows) < MIN_ISOTONIC_MATCHES:
            method = "sigmoid"
            report["reason"] = "sigmoid_until_1000_rows"
        calibrator = _HoldoutCalibrator(fitted_base, method)
        calibrator.fit_holdout(rows, labels, None if weights is None or len(weights) == 0 else weights)
        report["method"] = method
        return report, calibrator


class _MarketAwareCalibrator(ClassifierMixin, BaseEstimator):
    """Applies the calibrator that belongs to the row's market."""

    def __init__(self, estimator=None, match_calibrator=None, set_calibrator=None, market_index=None):
        self.estimator = estimator
        self.match_calibrator = match_calibrator
        self.set_calibrator = set_calibrator
        self.market_index = market_index

    def predict_proba(self, rows):
        matrix = np.asarray(rows, dtype=float)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        index = self.market_index
        if index is None or index >= matrix.shape[1]:
            return self._apply(self.match_calibrator, matrix)
        is_set = matrix[:, int(index)] >= 0.5
        output = np.zeros((len(matrix), 2), dtype=float)
        if np.any(~is_set):
            output[~is_set] = self._apply(self.match_calibrator, matrix[~is_set])
        if np.any(is_set):
            output[is_set] = self._apply(self.set_calibrator or self.match_calibrator, matrix[is_set])
        return output

    def predict(self, rows):
        return (self.predict_proba(rows)[:, 1] >= 0.5).astype(int)

    @property
    def classes_(self):
        return self.estimator.classes_

    def _apply(self, calibrator, rows):
        if calibrator is None:
            return self.estimator.predict_proba(rows)
        return calibrator.predict_proba(rows)


class _HoldoutCalibrator(ClassifierMixin, BaseEstimator):
    """Maps base probabilities with Platt or isotonic fit on a later holdout."""

    def __init__(self, estimator=None, method="sigmoid"):
        self.estimator = estimator
        self.method = method

    def fit_holdout(self, rows, labels, weights) -> None:
        raw = self._positive(rows)
        if self.method == "isotonic":
            model = IsotonicRegression(out_of_bounds="clip", y_min=1e-4, y_max=1.0 - 1e-4)
            model.fit(raw, labels, sample_weight=weights)
            self.calibrator_ = ("isotonic", model)
        else:
            logits = np.log(raw / (1.0 - raw)).reshape(-1, 1)
            model = LogisticRegression(C=1e6, solver="lbfgs")
            if weights is None:
                model.fit(logits, labels)
            else:
                model.fit(logits, labels, sample_weight=weights)
            self.calibrator_ = ("sigmoid", model)
        self.classes_ = np.asarray([0, 1])

    def predict_proba(self, rows):
        raw = self._positive(rows)
        kind, model = self.calibrator_
        if kind == "isotonic":
            positive = np.asarray(model.predict(raw), dtype=float)
        else:
            logits = np.log(raw / (1.0 - raw)).reshape(-1, 1)
            positive = model.predict_proba(logits)[:, 1]
        positive = np.clip(positive, 1e-6, 1.0 - 1e-6)
        return np.column_stack([1.0 - positive, positive])

    def predict(self, rows):
        return (self.predict_proba(rows)[:, 1] >= 0.5).astype(int)

    def _positive(self, rows) -> np.ndarray:
        probs = self.estimator.predict_proba(rows)
        classes = [int(item) for item in self.estimator.classes_]
        column = classes.index(1) if 1 in classes else 1
        return np.clip(np.asarray(probs[:, column], dtype=float), 1e-6, 1.0 - 1e-6)


def _holdout_cut(count: int, fraction: float) -> int:
    """Index where the calibration holdout starts. Both sides stay non-empty when possible."""
    if count < 2:
        return count
    holdout = int(round(count * float(fraction)))
    holdout = min(max(holdout, 1), count - 1)
    return count - holdout
