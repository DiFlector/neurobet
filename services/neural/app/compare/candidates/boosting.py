"""HistGradientBoosting, then CatBoost on CPU when the package is installed.

The first pass picks the learning rate with early stopping on validation.
The second pass moves one setting at a time around that point.
"""

from typing import Optional, Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss

from ..rows import CompareRow, feature_vector
from .logistic import _matrix

CAT_COLUMNS = ("surface", "tour_level", "round_name", "best_of")


class HistGradientCandidate:
    name = "D_hist_gradient_boosting"

    def __init__(self):
        self.model = None
        self.trials = []
        self.params = {
            "learning_rate": 0.05,
            "max_iter": 200,
            "max_depth": 4,
            "max_leaf_nodes": 15,
            "min_samples_leaf": 20,
            "l2_regularization": 1.0,
        }

    def select(self, train: Sequence[CompareRow], validation: Sequence[CompareRow]) -> None:
        x_train, y_train, w_train = _matrix(train)
        x_val, y_val, _w_val = _matrix(validation)
        if len(set(y_train.tolist())) < 2:
            self.fit(train)
            return
        best_rate, best_loss = 0.05, None
        for rate in (0.03, 0.05, 0.1):
            model = self._build({**self.params, "learning_rate": rate, "max_iter": 400})
            model.fit(x_train, y_train, sample_weight=w_train)
            loss = self._loss(model, x_val, y_val)
            self.trials.append({"learning_rate": rate, "log_loss": round(float(loss), 6)})
            if best_loss is None or loss < best_loss:
                best_rate, best_loss = rate, loss
        self.params["learning_rate"] = best_rate
        axes = {
            "max_depth": (3, 4, 6),
            "max_leaf_nodes": (7, 15, 31),
            "min_samples_leaf": (10, 20, 50),
            "l2_regularization": (0.0, 1.0, 10.0),
        }
        for key, values in axes.items():
            chosen, chosen_loss = self.params[key], None
            for value in values:
                trial = dict(self.params)
                trial[key] = value
                model = self._build(trial)
                model.fit(x_train, y_train, sample_weight=w_train)
                loss = self._loss(model, x_val, y_val)
                self.trials.append({key: value, "log_loss": round(float(loss), 6)})
                if chosen_loss is None or loss < chosen_loss:
                    chosen, chosen_loss = value, loss
            self.params[key] = chosen
        self.fit(train)

    def fit(self, rows: Sequence[CompareRow]) -> None:
        x, y, w = _matrix(rows)
        self.model = self._build(self.params)
        self.model.fit(x, y, sample_weight=w)

    def predict(self, rows: Sequence[CompareRow]) -> np.ndarray:
        x, _y, _w = _matrix(rows)
        return self.model.predict_proba(x)[:, 1]

    @staticmethod
    def _build(params: dict) -> HistGradientBoostingClassifier:
        return HistGradientBoostingClassifier(
            learning_rate=params["learning_rate"],
            max_iter=params["max_iter"],
            max_depth=params["max_depth"],
            max_leaf_nodes=params["max_leaf_nodes"],
            min_samples_leaf=params["min_samples_leaf"],
            l2_regularization=params["l2_regularization"],
            random_state=42,
        )

    @staticmethod
    def _loss(model, x_val, y_val) -> float:
        if len(set(y_val.tolist())) < 2:
            return 1.0
        return float(log_loss(y_val, model.predict_proba(x_val)[:, 1], labels=[0, 1]))


class CatBoostCandidate:
    name = "D_catboost"

    def __init__(self):
        self.model = None
        self.available = False
        try:
            import catboost  # noqa: F401

            self.available = True
        except ImportError:
            self.available = False

    def select(self, train: Sequence[CompareRow], validation: Sequence[CompareRow]) -> None:
        if not self.available:
            return
        self._fit(train, validation)

    def fit(self, rows: Sequence[CompareRow]) -> None:
        if not self.available:
            return
        self._fit(rows, None)

    def predict(self, rows: Sequence[CompareRow]) -> np.ndarray:
        if self.model is None:
            return np.full(len(rows), 0.5)
        frame = _frame(rows)
        return np.asarray(self.model.predict_proba(frame)[:, 1], dtype=float)

    def _fit(self, train: Sequence[CompareRow], validation: Optional[Sequence[CompareRow]]) -> None:
        from catboost import CatBoostClassifier, Pool

        cat_idx = [13, 14, 15, 16]
        train_pool = Pool(_frame(train), [row.label for row in train], cat_features=cat_idx)
        eval_pool = None
        if validation:
            eval_pool = Pool(_frame(validation), [row.label for row in validation], cat_features=cat_idx)
        self.model = CatBoostClassifier(
            iterations=300,
            learning_rate=0.05,
            depth=6,
            loss_function="Logloss",
            random_seed=42,
            thread_count=2,
            verbose=False,
            allow_writing_files=False,
        )
        self.model.fit(train_pool, eval_set=eval_pool, use_best_model=eval_pool is not None)


def _frame(rows: Sequence[CompareRow]):
    data = []
    for row in rows:
        data.append(
            feature_vector(row)
            + [row.surface or "unknown", row.tour_level or "unknown", row.round_name or "unknown", str(row.best_of)]
        )
    return np.asarray(data, dtype=object)
