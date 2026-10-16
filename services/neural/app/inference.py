"""ML Predictor: Loads trained checkpoints and computes calibrated MLPrediction contracts."""

import os
import uuid
from typing import Any, Dict, List, Optional
import joblib
import numpy as np

from contracts import FeatureVector, MLPrediction, OutcomePrediction

from .commit import MIN_COMMIT_EDGE, best_edge, side_odds
from .strategy import certainty


class MLPredictor:
    """
    Inference engine that loads calibrated model checkpoints and generates
    MLPrediction contracts with true probabilities, fair odds, edge, and expected value.
    """

    def __init__(self, checkpoint_path: Optional[str] = None):
        self.checkpoint_path = checkpoint_path
        self.checkpoint_data: Optional[Dict[str, Any]] = None
        if checkpoint_path and os.path.exists(checkpoint_path):
            self.load(checkpoint_path)

    def load(self, checkpoint_path: str) -> None:
        """Loads serialized checkpoint artifact."""
        self.checkpoint_path = checkpoint_path
        self.checkpoint_data = joblib.load(checkpoint_path)

    @classmethod
    def load_latest(
        cls,
        sport_code: str = "tennis",
        market: str = "match_winner",
        model_type: str = "lr",
        checkpoint_dir: str = "/app/models/checkpoints",
    ) -> "MLPredictor":
        """Loads the exact latest checkpoint file for this sport and model type."""
        path = os.path.join(checkpoint_dir, f"{sport_code}_{market}_{model_type}_latest.joblib")
        if not os.path.isfile(path):
            return cls(checkpoint_path=None)
        return cls(checkpoint_path=path)

    def predict(
        self,
        feature_vector: FeatureVector,
        odds_a: Optional[float] = None,
        odds_b: Optional[float] = None,
        min_edge_threshold: float = MIN_COMMIT_EDGE,
        is_shadow: bool = False,
        peer_probabilities: Optional[List[float]] = None,
        segment_reliability: Optional[float] = None,
    ) -> MLPrediction:
        """
        Calculates calibrated prediction from FeatureVector.
        Ensures prediction contains model_version, feature_version, fair_odds, and edge.
        """
        if not self.checkpoint_data:
            raise RuntimeError("No model checkpoint loaded into MLPredictor")

        model = self.checkpoint_data["model"]
        feature_names = self.checkpoint_data["feature_names"]
        model_version = self.checkpoint_data.get("version_tag", "v1.0.0_baseline")
        market = self.checkpoint_data.get("market", "match_winner")

        # Prepare 1xD feature row matching model training columns
        x_row = [float(feature_vector.features.get(k, 0.0)) for k in feature_names]
        X = np.asarray([x_row], dtype=float)

        # Calibrated probability
        probs = model.predict_proba(X)[0]
        prob_a = round(float(probs[1]), 4)
        prob_b = round(float(probs[0]), 4)

        # Ensure probabilities sum to 1.0
        prob_sum = prob_a + prob_b
        if prob_sum > 0:
            prob_a = round(prob_a / prob_sum, 4)
            prob_b = round(prob_b / prob_sum, 4)

        # Bookmaker odds. A missing quote stays missing so the side cannot commit on a default price.
        feat_odds_a, feat_odds_b = side_odds(feature_vector.features)
        bm_odds_a = float(odds_a) if odds_a else feat_odds_a
        bm_odds_b = float(odds_b) if odds_b else feat_odds_b

        # Fair odds (strictly > 1.0)
        fair_a = max(1.01, round(1.0 / max(0.001, prob_a), 2))
        fair_b = max(1.01, round(1.0 / max(0.001, prob_b), 2))

        # Edge: (model_prob * odds) - 1. No price means this side has no edge.
        edge_a = round((prob_a * bm_odds_a) - 1.0, 4) if bm_odds_a > 1.0 else -1.0
        edge_b = round((prob_b * bm_odds_b) - 1.0, 4) if bm_odds_b > 1.0 else -1.0
        commit_edge = best_edge(prob_a, prob_b, bm_odds_a, bm_odds_b)

        # Expected Value: prob * (odds - 1) - (1 - prob)
        ev_a = round(prob_a * (bm_odds_a - 1.0) - (1.0 - prob_a), 4)
        ev_b = round(prob_b * (bm_odds_b - 1.0) - (1.0 - prob_b), 4)

        # Confidence is agreement across models, scaled by calibration reliability.
        # Distance from 0.5 is not a measure of uncertainty.
        if peer_probabilities is not None or segment_reliability is not None:
            reliability = 1.0 if segment_reliability is None else segment_reliability
            conf = certainty([prob_a, *[float(item) for item in (peer_probabilities or [])]], reliability)
            conf_a = conf
            conf_b = conf
        else:
            conf_a = round(min(1.0, abs(prob_a - 0.5) * 2.0), 4)
            conf_b = round(min(1.0, abs(prob_b - 0.5) * 2.0), 4)

        is_team_sport = feature_vector.sport_code in ("basketball", "football", "hockey", "volleyball")
        outcome_a = "team_a" if is_team_sport else "player_a"
        outcome_b = "team_b" if is_team_sport else "player_b"

        outcomes = [
            OutcomePrediction(
                outcome=outcome_a,
                model_probability=prob_a,
                bookmaker_odds=bm_odds_a,
                fair_odds=fair_a,
                edge=edge_a,
                expected_value=ev_a,
                confidence=conf_a,
            ),
            OutcomePrediction(
                outcome=outcome_b,
                model_probability=prob_b,
                bookmaker_odds=bm_odds_b,
                fair_odds=fair_b,
                edge=edge_b,
                expected_value=ev_b,
                confidence=conf_b,
            ),
        ]

        # Determine best outcome. The commit gate is the same 2% edge used when building the training set.
        best = outcome_a if edge_a >= edge_b else outcome_b
        has_positive_edge = bool(commit_edge >= min_edge_threshold)

        return MLPrediction(
            prediction_id=f"pred_{uuid.uuid4().hex[:12]}",
            event_id=feature_vector.event_id,
            sport_code=feature_vector.sport_code,
            model_version=model_version,
            market=market,
            outcomes=outcomes,
            best_outcome=best if has_positive_edge else None,
            has_positive_edge=has_positive_edge,
            is_shadow=is_shadow,
        )
