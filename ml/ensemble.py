"""
Ensemble Blender & Value Bet Predictor
Combines LightGBM and PyTorch models with progress visualization.
"""

import numpy as np
from typing import Tuple, List, Dict, Any

class EnsemblePredictor:
    def __init__(self, lgb_model, pytorch_model, weight_lgb: float = 0.5):
        self.lgb_model = lgb_model
        self.pytorch_model = pytorch_model
        self.weight_lgb = weight_lgb

    def predict_proba(self, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Blends LightGBM and PyTorch probabilities:
        P_ensemble = w * P_lgb + (1 - w) * P_pytorch
        """
        lgb_p_1x2, lgb_p_over = self.lgb_model.predict_proba(X)
        pt_p_1x2, pt_p_over = self.pytorch_model.predict_proba(X)

        w = self.weight_lgb
        blend_1x2 = w * lgb_p_1x2 + (1.0 - w) * pt_p_1x2
        blend_over = w * lgb_p_over + (1.0 - w) * pt_p_over

        return blend_1x2, blend_over

    def find_value_bets(
        self, X: np.ndarray, samples: List[Dict[str, Any]], min_ev: float = 0.05
    ) -> List[Dict[str, Any]]:
        """
        Identifies Value Bets (+EV) where Expected Value > min_ev (e.g. 5% edge).
        """
        probs_1x2, probs_over = self.predict_proba(X)
        value_bets = []

        outcomes_map = [(0, "921", "П1"), (1, "922", "Ничья"), (2, "923", "П2")]

        for idx, sample in enumerate(samples):
            eid = sample.get("event_id")
            match_name = sample.get("match_name")
            sport_path = sample.get("sport_path")
            score = f"{sample.get('score_1')}:{sample.get('score_2')}"
            odds_dict = sample.get("odds_vector", {})

            for class_idx, fid_str, label in outcomes_map:
                o_val = odds_dict.get(fid_str)
                if o_val and float(o_val) > 1.0:
                    bookie_odds = float(o_val)
                    model_prob = probs_1x2[idx][class_idx]
                    implied_prob = 1.0 / bookie_odds
                    ev = (model_prob * bookie_odds) - 1.0

                    if ev >= min_ev:
                        value_bets.append({
                            "event_id": eid,
                            "sport_path": sport_path,
                            "match_name": match_name,
                            "current_score": score,
                            "outcome": label,
                            "factor_id": fid_str,
                            "bookmaker_odds": bookie_odds,
                            "implied_probability": round(implied_prob, 4),
                            "model_probability": round(float(model_prob), 4),
                            "expected_value_ev": round(float(ev), 4),
                            "ev_percent": f"{round(float(ev) * 100, 2)}%"
                        })

        return value_bets
