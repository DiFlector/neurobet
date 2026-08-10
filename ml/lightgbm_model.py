"""
LightGBM Sports Odds Classifier & Predictor
Gradient Boosted Trees model for predicting match outcomes and probability distributions.
"""

import pickle
import numpy as np
import lightgbm as lgb
from typing import Tuple, Dict, Any, List

class LightGBMPredictor:
    def __init__(self, num_classes: int = 3):
        self.num_classes = num_classes
        self.model_1x2 = lgb.LGBMClassifier(
            n_estimators=100,
            learning_rate=0.05,
            num_leaves=31,
            random_state=42,
            verbosity=-1
        )
        self.model_over = lgb.LGBMClassifier(
            n_estimators=100,
            learning_rate=0.05,
            num_leaves=31,
            random_state=42,
            verbosity=-1
        )
        self.is_trained = False

    def train(self, X_train: np.ndarray, y_1x2_train: np.ndarray, y_over_train: np.ndarray):
        """Trains LightGBM models for 1X2 and Over/Under."""
        self.model_1x2.fit(X_train, y_1x2_train)
        self.model_over.fit(X_train, y_over_train)
        self.is_trained = True

    def predict_proba(self, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Returns:
            probs_1x2: Array of shape (N, 3) with probabilities for [Win 1, Draw, Win 2]
            probs_over: Array of shape (N, 2) with probabilities for [Under, Over]
        """
        if not self.is_trained:
            raise RuntimeError("Model is not trained yet!")

        probs_1x2 = self.model_1x2.predict_proba(X)
        # Ensure 3 columns even if some classes weren't present in small samples
        if probs_1x2.shape[1] < 3:
            full_probs = np.zeros((len(X), 3), dtype=np.float32)
            for idx, cls in enumerate(self.model_1x2.classes_):
                full_probs[:, cls] = probs_1x2[:, idx]
            probs_1x2 = full_probs

        probs_over = self.model_over.predict_proba(X)
        return probs_1x2, probs_over

    def get_feature_importance(self, feature_names: List[str]) -> List[Tuple[str, float]]:
        """Returns feature importance ranking."""
        if not self.is_trained:
            return []
        importances = self.model_1x2.feature_importances_
        ranking = sorted(zip(feature_names, importances), key=lambda x: x[1], reverse=True)
        return ranking

    def save(self, filepath: str):
        with open(filepath, "wb") as f:
            pickle.dump({"1x2": self.model_1x2, "over": self.model_over}, f)

    def load(self, filepath: str):
        with open(filepath, "rb") as f:
            data = pickle.load(f)
            self.model_1x2 = data["1x2"]
            self.model_over = data["over"]
            self.is_trained = True
