"""Averaged trees. The validation block chooses the forest, not the frozen test."""

from typing import Sequence

import numpy as np
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
from sklearn.metrics import log_loss

from ..rows import CompareRow
from .logistic import _matrix


class TreeBaggingCandidate:
    name = "F_tree_bagging"

    def __init__(self):
        self.model = None
        self.chosen = {"kind": "rf", "n_estimators": 100}
        self.trials = []

    def select(self, train: Sequence[CompareRow], validation: Sequence[CompareRow]) -> None:
        x_train, y_train, w_train = _matrix(train)
        x_val, y_val, _w_val = _matrix(validation)
        if len(set(y_train.tolist())) < 2 or len(set(y_val.tolist())) < 2:
            self._fit(x_train, y_train, w_train, "rf", 100)
            return
        best_loss = None
        for kind in ("rf", "et"):
            for trees in (100, 300):
                model = self._build(kind, trees)
                model.fit(x_train, y_train, sample_weight=w_train)
                loss = float(log_loss(y_val, model.predict_proba(x_val)[:, 1], labels=[0, 1]))
                self.trials.append({"kind": kind, "n_estimators": trees, "log_loss": round(loss, 6)})
                if best_loss is None or loss < best_loss:
                    best_loss = loss
                    self.model = model
                    self.chosen = {"kind": kind, "n_estimators": trees}

    def fit(self, rows: Sequence[CompareRow]) -> None:
        x, y, w = _matrix(rows)
        self._fit(x, y, w, self.chosen["kind"], self.chosen["n_estimators"])

    def predict(self, rows: Sequence[CompareRow]) -> np.ndarray:
        x, _y, _w = _matrix(rows)
        return self.model.predict_proba(x)[:, 1]

    def _fit(self, x, y, w, kind: str, trees: int) -> None:
        self.chosen = {"kind": kind, "n_estimators": trees}
        self.model = self._build(kind, trees)
        self.model.fit(x, y, sample_weight=w)

    @staticmethod
    def _build(kind: str, trees: int):
        common = {
            "n_estimators": trees,
            "min_samples_leaf": 5,
            "max_depth": 8,
            "n_jobs": 2,
            "random_state": 42,
        }
        if kind == "et":
            return ExtraTreesClassifier(**common)
        return RandomForestClassifier(**common)
