"""Model pipelines: Logistic Regression and Gradient Boosting.

Calibration is fit on a later slice of matches, never on random rows of the
same match. `CalibratedClassifierCV(cv=3)` mixed snapshots of one match
between the base fit and the calibrator.
"""

from typing import Any, Optional, Sequence

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin, clone
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .dataset import example_weights, positive_probabilities, project_market, stack_matches

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
    learning_rate: float = 0.02,
    max_iter: int = 100,
    max_depth: int = 3,
    random_state: int = 42,
    max_leaf_nodes: Optional[int] = 8,
    min_samples_leaf: int = 50,
    l2_regularization: float = 5.0,
    early_stopping: bool = True,
) -> HistGradientBoostingClassifier:
    """Uncalibrated regularized histogram gradient boosting for numeric features."""
    return HistGradientBoostingClassifier(
        learning_rate=learning_rate,
        max_iter=max_iter,
        max_depth=max_depth,
        max_leaf_nodes=max_leaf_nodes,
        min_samples_leaf=min_samples_leaf,
        l2_regularization=l2_regularization,
        early_stopping=early_stopping,
        random_state=random_state,
    )


def build_gradient_boosting_regressor(
    learning_rate: float = 0.02,
    max_iter: int = 100,
    max_depth: int = 3,
    random_state: int = 42,
    max_leaf_nodes: Optional[int] = 8,
    min_samples_leaf: int = 50,
    l2_regularization: float = 5.0,
    early_stopping: bool = True,
) -> HistGradientBoostingRegressor:
    """Uncalibrated regularized histogram gradient boosting regressor for residual target."""
    return HistGradientBoostingRegressor(
        loss="squared_error",
        learning_rate=learning_rate,
        max_iter=max_iter,
        max_depth=max_depth,
        max_leaf_nodes=max_leaf_nodes,
        min_samples_leaf=min_samples_leaf,
        l2_regularization=l2_regularization,
        early_stopping=early_stopping,
        random_state=random_state,
    )


def logit_np(prob: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(prob, dtype=float), 1e-4, 1.0 - 1e-4)
    return np.log(clipped / (1.0 - clipped))


def sigmoid_np(logits: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.asarray(logits, dtype=float)))


class ResidualMarketEstimator(ClassifierMixin, BaseEstimator):
    """Residual model that predicts a bounded logit adjustment over market implied probability.

    When base_estimator is a regressor, it trains directly on the smoothed logit residual:
        r = clip(logit(y*) - logit(P_market), -max_offset, max_offset), where y* = 0.05 + 0.90 * y
    When base_estimator is a classifier, it fits on y and shifts logits:
        delta = logit(P_base) - logit(P_market)
    In both cases:
        P = sigmoid(logit(P_market) + clip(delta_logit, -max_offset, max_offset))
    """

    def __init__(
        self,
        base_estimator: Any = None,
        max_offset: float = 0.25,
        market_feature_index: Optional[int] = None,
    ):
        self.base_estimator = base_estimator
        self.max_offset = max_offset
        self.market_feature_index = market_feature_index

    def _is_regressor_base(self) -> bool:
        est = getattr(self, "base_estimator_", self.base_estimator)
        if est is None:
            return False
        if isinstance(est, Pipeline):
            est = est.steps[-1][1]
        return not hasattr(est, "predict_proba")

    def fit(self, X: Any, y: Any, sample_weight: Optional[Any] = None) -> "ResidualMarketEstimator":
        self.base_estimator_ = clone(self.base_estimator)
        rows = np.asarray(X, dtype=float)
        labels = np.asarray(y, dtype=int)
        if self.market_feature_index is not None and self.market_feature_index < rows.shape[1] and rows.shape[1] > 1:
            raw_mkt = rows[:, self.market_feature_index]
            market_probs = np.where((raw_mkt >= 0.01) & (raw_mkt <= 0.99), raw_mkt, 0.5)
            base_X = np.delete(rows, self.market_feature_index, axis=1)
        else:
            market_probs = np.full(len(rows), 0.5)
            base_X = rows

        if self._is_regressor_base():
            y_smooth = 0.05 + 0.90 * labels.astype(float)
            target_resid = logit_np(y_smooth) - logit_np(market_probs)
            target_resid = np.clip(target_resid, -self.max_offset, self.max_offset)
            fit_estimator(self.base_estimator_, base_X, target_resid, sample_weight)
        else:
            fit_estimator(self.base_estimator_, base_X, labels, sample_weight)

        self.classes_ = np.asarray([0, 1])
        return self

    def predict_proba(self, X: Any) -> np.ndarray:
        matrix = np.asarray(X, dtype=float)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)

        if self.market_feature_index is not None and self.market_feature_index < matrix.shape[1]:
            raw_mkt = matrix[:, self.market_feature_index]
            market_probs = np.where((raw_mkt >= 0.01) & (raw_mkt <= 0.99), raw_mkt, 0.5)
            if matrix.shape[1] > 1:
                base_X = np.delete(matrix, self.market_feature_index, axis=1)
            else:
                base_X = matrix
        else:
            market_probs = np.full(len(matrix), 0.5)
            base_X = matrix

        market_logits = logit_np(market_probs)
        if self._is_regressor_base():
            pred_resid = np.asarray(self.base_estimator_.predict(base_X), dtype=float)
            shift = np.clip(pred_resid, -self.max_offset, self.max_offset)
        else:
            base_probs = positive_probabilities(self.base_estimator_, base_X)
            base_logits = logit_np(base_probs)
            shift = np.clip(base_logits - market_logits, -self.max_offset, self.max_offset)

        final_probs = sigmoid_np(market_logits + shift)
        final_probs = np.clip(final_probs, 1e-4, 1.0 - 1e-4)
        return np.column_stack([1.0 - final_probs, final_probs])

    def predict(self, X: Any) -> np.ndarray:
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)


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
    learning_rate: float = 0.02,
    max_iter: int = 100,
    max_depth: int = 3,
    random_state: int = 42,
    calibration_method: str = "isotonic",
    calibration_fraction: float = DEFAULT_CALIBRATION_FRACTION,
    max_leaf_nodes: Optional[int] = 8,
    min_samples_leaf: int = 50,
    l2_regularization: float = 5.0,
    early_stopping: bool = True,
) -> "TemporalCalibratedEstimator":
    """Regularized gradient boosting whose calibrator sees only the later matches."""
    return TemporalCalibratedEstimator(
        estimator=build_gradient_boosting_estimator(
            learning_rate=learning_rate,
            max_iter=max_iter,
            max_depth=max_depth,
            random_state=random_state,
            max_leaf_nodes=max_leaf_nodes,
            min_samples_leaf=min_samples_leaf,
            l2_regularization=l2_regularization,
            early_stopping=early_stopping,
        ),
        method=calibration_method,
        calibration_fraction=calibration_fraction,
    )


def build_residual_boosting_pipeline(
    learning_rate: float = 0.02,
    max_iter: int = 100,
    max_depth: int = 3,
    random_state: int = 42,
    calibration_method: str = "sigmoid",
    calibration_fraction: float = DEFAULT_CALIBRATION_FRACTION,
    max_offset: float = 0.25,
    market_feature_index: Optional[int] = None,
) -> "TemporalCalibratedEstimator":
    """Residual gradient boosting bounded against market probability via true residual target."""
    base = build_gradient_boosting_regressor(
        learning_rate=learning_rate,
        max_iter=max_iter,
        max_depth=max_depth,
        random_state=random_state,
        max_leaf_nodes=8,
        min_samples_leaf=50,
        l2_regularization=5.0,
        early_stopping=True,
    )
    residual = ResidualMarketEstimator(
        base_estimator=base,
        max_offset=max_offset,
        market_feature_index=market_feature_index,
    )
    return TemporalCalibratedEstimator(
        estimator=residual,
        method=calibration_method,
        calibration_fraction=calibration_fraction,
    )


class TennisMarketOffsetEstimator(ClassifierMixin, BaseEstimator):
    """Market-logit XGBoost heads for match and set outcomes (tennis v4)."""

    def __init__(
        self,
        max_depth: int = 3,
        learning_rate: float = 0.03,
        n_estimators: int = 300,
        reg_lambda: float = 10.0,
        random_state: int = 42,
        n_jobs: int = 2,
        early_stopping_rounds: int = 30,
    ):
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.n_estimators = n_estimators
        self.reg_lambda = reg_lambda
        self.random_state = random_state
        self.n_jobs = n_jobs
        self.early_stopping_rounds = early_stopping_rounds

    def _market_logit(self, rows: np.ndarray) -> np.ndarray:
        from features.selection import SELECTED_TENNIS_FEATURES_V4

        index = SELECTED_TENNIS_FEATURES_V4.index("no_vig_prob_player_a")
        return logit_np(np.asarray(rows, dtype=float)[:, index])

    def _market_mask(self, rows: np.ndarray, family: str) -> np.ndarray:
        from features.selection import SELECTED_TENNIS_FEATURES_V4

        index = SELECTED_TENNIS_FEATURES_V4.index("market_is_set")
        values = np.asarray(rows, dtype=float)[:, index]
        return values >= 0.5 if family == "set" else values < 0.5

    def _new_model(self, n_estimators: Optional[int] = None):
        from xgboost import XGBClassifier

        return XGBClassifier(
            objective="binary:logistic",
            eval_metric="logloss",
            max_depth=int(self.max_depth),
            learning_rate=float(self.learning_rate),
            n_estimators=int(n_estimators or self.n_estimators),
            reg_lambda=float(self.reg_lambda),
            random_state=int(self.random_state),
            n_jobs=int(self.n_jobs),
            tree_method="hist",
        )

    @staticmethod
    def _split_matches(matches: Sequence[Any]) -> tuple[list, list, list]:
        """Chronological 60/20/20 split. Prefer outcome availability over overlapping live tapes."""
        ordered = [match for match in matches if len(match.rows) > 0]
        available = [getattr(match, "result_available_at", None) for match in ordered]
        if ordered and all(moment is not None for moment in available):
            ordered = [item for _moment, item in sorted(zip(available, ordered), key=lambda pair: (pair[0], pair[1].event_id))]
            first = max(1, int(len(ordered) * 0.60))
            second = max(first + 1, int(len(ordered) * 0.80))
            if second >= len(ordered):
                raise ValueError(f"Tennis v4 requires at least 5 priced matches per market, got {len(ordered)}.")
            train, early, calibration = ordered[:first], ordered[first:second], ordered[second:]
            if len({item.event_id for part in (train, early, calibration) for item in part}) != len(ordered):
                raise RuntimeError("Tennis v4 result-time split duplicated a match.")
            return train, early, calibration
        count = len(ordered)
        first = max(1, int(count * 0.60))
        second = max(first + 1, int(count * 0.80))
        if second >= count:
            raise ValueError(f"Tennis v4 requires at least 5 priced matches per market, got {count}.")
        train = ordered[:first]
        early = ordered[first:second]
        calibration = ordered[second:]

        def last_seen(group):
            return max(moment for item in group for moment in item.observed_at)

        def first_seen(group):
            return min(moment for item in group for moment in item.observed_at)

        while train and early and last_seen(train) >= first_seen(early):
            train.pop()
        while early and calibration and last_seen(early) >= first_seen(calibration):
            early.pop()
        if not train or not early or not calibration:
            raise ValueError("Tennis v4 temporal split is empty after purging overlapping matches.")
        return train, early, calibration

    def fit_matches(self, matches: Sequence[Any], feature_names: Sequence[str], loss_gamma: float = 0.0):
        """Fit each market head on earlier matches and calibrate offset on later matches."""
        del loss_gamma
        from .dataset import project_market, stack_matches

        names = list(feature_names)
        from features.selection import SELECTED_TENNIS_FEATURES_V4

        if names != SELECTED_TENNIS_FEATURES_V4:
            raise ValueError("Tennis v4 checkpoint must use the canonical ordered 23-feature contract.")
        self.feature_names_ = names
        self.models_ = {}
        self.alpha_ = {}
        self.head_report_ = {}
        self.classes_ = np.asarray([0, 1])

        for family in ("match", "set"):
            market_matches = [
                projected
                for item in matches
                if (projected := project_market(item, family)).rows
            ]
            train, early, calibration = self._split_matches(market_matches)
            X_train, y_train, weights = stack_matches(train)
            X_early, y_early, early_weights = stack_matches(early)
            X_cal, y_cal, calibration_weights = stack_matches(calibration)
            if len(set(y_train.tolist())) < 2:
                raise ValueError(f"Tennis v4 {family} head needs both classes in its training window.")
            model = self._new_model()
            model.set_params(early_stopping_rounds=int(self.early_stopping_rounds))
            base_train = self._market_logit(X_train)
            base_early = self._market_logit(X_early)
            model.fit(
                X_train,
                y_train,
                sample_weight=weights,
                base_margin=base_train,
                eval_set=[(X_early, y_early)],
                sample_weight_eval_set=[early_weights],
                base_margin_eval_set=[base_early],
                verbose=False,
            )
            self.models_[family] = model

            raw_cal = model.predict(X_cal, base_margin=self._market_logit(X_cal), output_margin=True)
            delta = np.asarray(raw_cal, dtype=float) - self._market_logit(X_cal)
            chosen_alpha = 0.0
            chosen_loss = float("inf")
            for alpha in (0.0, 0.25, 0.5, 0.75, 1.0):
                probability = sigmoid_np(self._market_logit(X_cal) + alpha * np.clip(delta, -0.35, 0.35))
                loss = _binary_log_loss(y_cal, probability, calibration_weights)
                if loss < chosen_loss - 1e-12:
                    chosen_alpha, chosen_loss = alpha, loss
            self.alpha_[family] = chosen_alpha
            self.head_report_[family] = {
                "train_matches": len(train),
                "early_stop_matches": len(early),
                "calibration_matches": len(calibration),
                "best_iteration": int(getattr(model, "best_iteration", model.n_estimators - 1)),
                "alpha": chosen_alpha,
                "calibration_log_loss": round(chosen_loss, 6),
            }
        return self

    def predict_proba(self, X: Any) -> np.ndarray:
        matrix = np.asarray(X, dtype=float)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        if matrix.shape[1] != len(self.feature_names_):
            raise ValueError(f"Tennis v4 expected {len(self.feature_names_)} features, received {matrix.shape[1]}.")
        result = np.empty(len(matrix), dtype=float)
        for family in ("match", "set"):
            mask = self._market_mask(matrix, family)
            if not np.any(mask):
                continue
            rows = matrix[mask]
            baseline = self._market_logit(rows)
            raw = self.models_[family].predict(rows, base_margin=baseline, output_margin=True)
            delta = np.asarray(raw, dtype=float) - baseline
            result[mask] = sigmoid_np(baseline + self.alpha_[family] * np.clip(delta, -0.35, 0.35))
        result = np.clip(result, 1e-4, 1.0 - 1e-4)
        return np.column_stack((1.0 - result, result))


def _binary_log_loss(labels: np.ndarray, probabilities: np.ndarray, sample_weight: Optional[np.ndarray] = None) -> float:
    """Binary log loss with match-equal weights for v4 alpha selection."""
    y = np.asarray(labels, dtype=float)
    p = np.clip(np.asarray(probabilities, dtype=float), 1e-12, 1.0 - 1e-12)
    loss = -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))
    if sample_weight is None:
        return float(np.mean(loss))
    weight = np.asarray(sample_weight, dtype=float)
    return float(np.average(loss, weights=weight))


def weighted_for_gamma(estimator: Any, rows: np.ndarray, labels: np.ndarray, weights: Optional[np.ndarray], gamma: float) -> Optional[np.ndarray]:
    """Second pass. gamma 0 keeps the weights the caller already built."""
    if float(gamma) <= 0.0 or weights is None or len(rows) < 8 or not _both_classes(np.asarray(labels)):
        return weights
    probe = fit_estimator(clone(estimator), rows, labels, weights)
    probabilities = positive_probabilities(probe, np.asarray(rows, dtype=float))
    return example_weights(weights, probabilities, labels, gamma)


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

    def fit_matches(self, matches: Sequence[Any], feature_names: Optional[Sequence[str]] = None, loss_gamma: float = 0.0):
        """Fit on whole matches. The last fraction is the calibration holdout.

        When rows name their market, the base model still sees every earlier row,
        but match and set each get a calibrator fit only on that family's later rows.
        loss_gamma 0 is the ordinary log loss. A positive value refits the base
        model once, with a higher weight on rows the first fit assigned to the wrong side.
        The calibrator still sees the original row weights.
        """
        ordered = [match for match in matches if len(match.rows) > 0]
        gamma = float(loss_gamma or 0.0)
        names = list(feature_names or [])
        self.market_feature_index_ = names.index("market_is_set") if "market_is_set" in names else None
        if hasattr(self.estimator, "market_feature_index") and getattr(self.estimator, "market_feature_index", None) is None:
            if "no_vig_prob_player_a" in names:
                self.estimator.market_feature_index = names.index("no_vig_prob_player_a")
        self.base_event_ids_ = []
        self.calibration_event_ids_ = []
        if len(ordered) < 2:
            rows, labels, weights = stack_matches(ordered)
            self._fit_split(rows, labels, weights, rows[:0], labels[:0], weights[:0] if len(weights) else weights, gamma)
            return self
        cut = _holdout_cut(len(ordered), self.calibration_fraction)
        earlier, later = ordered[:cut], ordered[cut:]
        self.base_event_ids_ = [str(match.event_id) for match in earlier]
        self.calibration_event_ids_ = [str(match.event_id) for match in later]
        base_rows, base_labels, base_weights = stack_matches(earlier)
        has_markets = self.market_feature_index_ is not None and any(match.row_markets for match in ordered)
        if not has_markets:
            calibration_matches = [project_market(match, "match") for match in later]
            calibration_matches = [match for match in calibration_matches if len(match.rows) > 0] or list(later)
            cal_rows, cal_labels, cal_weights = stack_matches(calibration_matches)
            self._fit_split(base_rows, base_labels, base_weights, cal_rows, cal_labels, cal_weights, gamma)
            return self
        self._fit_market_calibrators(base_rows, base_labels, base_weights, later, gamma)
        return self

    def predict_proba(self, X):
        return self.model_.predict_proba(X)

    def predict(self, X):
        return self.model_.predict(X)

    @property
    def classes_(self):
        return self.model_.classes_

    def _fit_split(self, base_rows, base_labels, base_weights, cal_rows, cal_labels, cal_weights, loss_gamma: float = 0.0) -> None:
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
        supplied = getattr(self, "_supplied_base", None)
        if not use_calibration:
            if supplied is not None:
                self._supplied_base = None
                self.model_ = supplied
            else:
                base_weights = weighted_for_gamma(self.estimator, base_rows, base_labels, base_weights, loss_gamma)
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
        if supplied is None:
            base_weights = weighted_for_gamma(self.estimator, base_rows, base_labels, base_weights, loss_gamma)
            fitted = fit_estimator(base, base_rows, base_labels, base_weights)
        else:
            fitted = supplied
            self._supplied_base = None
        self.model_ = _HoldoutCalibrator(fitted, method)
        self.applied_calibration_ = method
        self.model_.fit_holdout(cal_rows, cal_labels, cal_weights)
        self.calibrated_ = True
        self.calibration_report_ = {
            "match": {"method": method, "rows": int(len(cal_rows)), "reason": ""},
            "set": {"method": None, "rows": 0, "reason": "no_market_split"},
        }

    def _fit_market_calibrators(self, base_rows, base_labels, base_weights, later, loss_gamma: float = 0.0) -> None:
        """One base model. Each market family is calibrated only on its own later rows."""
        supplied = getattr(self, "_supplied_base", None)
        if supplied is None:
            base_weights = weighted_for_gamma(self.estimator, base_rows, base_labels, base_weights, loss_gamma)
            fitted = fit_estimator(clone(self.estimator), base_rows, base_labels, base_weights)
        else:
            fitted = supplied
            self._supplied_base = None
        match_rows, match_labels, match_weights = stack_matches(
            [match for match in (project_market(item, "match") for item in later) if len(match.rows) > 0]
        )
        set_rows, set_labels, set_weights = stack_matches(
            [match for match in (project_market(item, "set") for item in later) if len(match.rows) > 0]
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
