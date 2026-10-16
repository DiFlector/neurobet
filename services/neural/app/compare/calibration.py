"""Calibrators fit on validation probabilities, never on the test matches."""

from typing import Optional, Sequence, Set

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

MIN_ISOTONIC_MATCHES = 1000


class ProbabilityCalibrator:
    def __init__(self, method: str = "none"):
        self.method = method
        self.skipped_reason: Optional[str] = None
        self._model = None

    def fit(
        self,
        probabilities: np.ndarray,
        labels: np.ndarray,
        event_ids: Sequence[str],
        forbidden_event_ids: Set[str],
        sample_weight: Optional[np.ndarray] = None,
    ) -> "ProbabilityCalibrator":
        overlap = set(event_ids) & set(forbidden_event_ids)
        if overlap:
            raise RuntimeError("Калибратор увидел матчи теста.")
        probs = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
        y = np.asarray(labels, dtype=int)
        matches = len(set(event_ids))
        method = self.method
        if method == "isotonic" and matches < MIN_ISOTONIC_MATCHES:
            self.skipped_reason = f"isotonic_needs_{MIN_ISOTONIC_MATCHES}_matches"
            method = "none"
            self.method = "none"
        if method == "none" or len(y) < 8 or len(set(y.tolist())) < 2:
            self._model = None
            if method != "none" and self.skipped_reason is None:
                self.skipped_reason = "too_few_rows"
                self.method = "none"
            return self
        if method == "sigmoid":
            logits = np.log(probs / (1.0 - probs)).reshape(-1, 1)
            model = LogisticRegression(C=1.0, solver="lbfgs")
            if sample_weight is None:
                model.fit(logits, y)
            else:
                model.fit(logits, y, sample_weight=sample_weight)
            self._model = ("sigmoid", model)
            return self
        model = IsotonicRegression(out_of_bounds="clip", y_min=1e-4, y_max=1 - 1e-4)
        model.fit(probs, y, sample_weight=sample_weight)
        self._model = ("isotonic", model)
        return self

    def transform(self, probabilities: np.ndarray) -> np.ndarray:
        probs = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
        if self._model is None:
            return probs
        kind, model = self._model
        if kind == "sigmoid":
            logits = np.log(probs / (1.0 - probs)).reshape(-1, 1)
            return model.predict_proba(logits)[:, 1]
        return np.asarray(model.predict(probs), dtype=float)
