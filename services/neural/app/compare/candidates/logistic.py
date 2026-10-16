"""Logistic regression. C is chosen on the validation block only."""

from typing import Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ..rows import CompareRow, feature_vector

C_GRID = (0.01, 0.1, 1.0, 10.0, 100.0)


def _matrix(rows: Sequence[CompareRow]) -> tuple:
    x = np.asarray([feature_vector(row) for row in rows], dtype=float)
    y = np.asarray([row.label for row in rows], dtype=int)
    weights = []
    counts = {}
    for row in rows:
        counts[row.event_id] = counts.get(row.event_id, 0) + 1
    for row in rows:
        weights.append(1.0 / counts[row.event_id])
    return x, y, np.asarray(weights, dtype=float)


class LogisticCandidate:
    name = "C_logistic"

    def __init__(self):
        self.model = None
        self.chosen_c = 1.0
        self.trials = []

    def select(self, train: Sequence[CompareRow], validation: Sequence[CompareRow]) -> None:
        x_train, y_train, w_train = _matrix(train)
        x_val, y_val, _w_val = _matrix(validation)
        best_loss = None
        best = None
        if len(set(y_train.tolist())) < 2 or len(set(y_val.tolist())) < 2:
            self._fit(x_train, y_train, w_train, 1.0)
            return
        for c_value in C_GRID:
            model = self._build(c_value)
            model.fit(x_train, y_train, classifier__sample_weight=w_train)
            loss = log_loss(y_val, model.predict_proba(x_val)[:, 1], labels=[0, 1])
            self.trials.append({"C": c_value, "log_loss": round(float(loss), 6)})
            if best_loss is None or loss < best_loss:
                best_loss = loss
                best = (c_value, model)
        self.chosen_c, self.model = best

    def fit(self, rows: Sequence[CompareRow]) -> None:
        x, y, w = _matrix(rows)
        self._fit(x, y, w, self.chosen_c)

    def predict(self, rows: Sequence[CompareRow]) -> np.ndarray:
        x, _y, _w = _matrix(rows)
        return self.model.predict_proba(x)[:, 1]

    def _fit(self, x, y, w, c_value: float) -> None:
        self.chosen_c = c_value
        self.model = self._build(c_value)
        self.model.fit(x, y, classifier__sample_weight=w)

    @staticmethod
    def _build(c_value: float) -> Pipeline:
        return Pipeline(
            [
                ("scaler", StandardScaler()),
                ("classifier", LogisticRegression(C=c_value, max_iter=1000, solver="lbfgs")),
            ]
        )
