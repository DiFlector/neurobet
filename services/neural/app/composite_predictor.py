"""Production inference for the single composite tennis shadow line."""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, Optional

import joblib
import numpy as np

from contracts import FeatureVector, MLPrediction, OutcomePrediction

from .commit import MIN_COMMIT_EDGE, best_edge, side_odds
from .composite_vote import COMPONENT_LABELS, COMPONENT_ORDER, DISPLAY_LABEL
from .strategy import stake_gate


def is_composite_checkpoint(checkpoint: Optional[Dict[str, Any]]) -> bool:
    return bool(checkpoint) and str(checkpoint.get("kind") or "") == "composite"


def load_predictor(checkpoint_path: str):
    """Load a sklearn line or the composite bundle from one registry artifact."""
    from .inference import MLPredictor

    if not checkpoint_path or not os.path.exists(checkpoint_path):
        return MLPredictor(checkpoint_path=checkpoint_path)
    payload = joblib.load(checkpoint_path)
    if is_composite_checkpoint(payload):
        return CompositePredictor(checkpoint_data=payload, checkpoint_path=checkpoint_path)
    predictor = MLPredictor(checkpoint_path=None)
    predictor.checkpoint_path = checkpoint_path
    predictor.checkpoint_data = payload
    return predictor


def eligible_side(
    probability_a: float,
    odds_a: float,
    odds_b: float,
    market: str,
) -> Dict[str, Any]:
    """Side a line may confirm on this snapshot, using the live edge and stake gates."""
    probability_a = float(probability_a)
    probability_b = round(1.0 - probability_a, 4)
    price_a = float(odds_a or 0.0)
    price_b = float(odds_b or 0.0)
    edge_a = round((probability_a * price_a) - 1.0, 4) if price_a > 1.0 else -1.0
    edge_b = round((probability_b * price_b) - 1.0, 4) if price_b > 1.0 else -1.0
    side = "player_a" if edge_a >= edge_b else "player_b"
    probability = probability_a if side == "player_a" else probability_b
    odds = price_a if side == "player_a" else price_b
    edge = edge_a if side == "player_a" else edge_b
    if best_edge(probability_a, probability_b, price_a, price_b) < MIN_COMMIT_EDGE:
        return {
            "side": None,
            "probability": round(probability, 4),
            "odds": odds,
            "edge": edge,
            "reason": "NO_EDGE",
        }
    allowed, reason = stake_gate(probability, odds, market)
    if not allowed:
        return {
            "side": None,
            "probability": round(probability, 4),
            "odds": odds,
            "edge": edge,
            "reason": reason,
        }
    return {
        "side": side,
        "probability": round(probability, 4),
        "odds": odds,
        "edge": edge,
        "reason": "",
    }


class CompositePredictor:
    """Three pinned lines behind one shadow model name and one version tag."""

    def __init__(self, checkpoint_data: Dict[str, Any], checkpoint_path: Optional[str] = None):
        self.checkpoint_path = checkpoint_path
        self.checkpoint_data = checkpoint_data
        self._network = None

    def component_votes(
        self,
        feature_vector: FeatureVector,
        odds_a: Optional[float],
        odds_b: Optional[float],
        market: str,
    ) -> list:
        prices = side_odds(feature_vector.features or {})
        price_a = float(odds_a) if odds_a else prices[0]
        price_b = float(odds_b) if odds_b else prices[1]
        names = list(self.checkpoint_data["feature_names"])
        row = np.asarray([[float((feature_vector.features or {}).get(name, 0.0)) for name in names]], dtype=float)
        votes = []
        for component_id in COMPONENT_ORDER:
            component = self.checkpoint_data["components"][component_id]
            probability = self._probability(component_id, component, row, price_a, price_b, market)
            decision = eligible_side(probability, price_a, price_b, market)
            votes.append(
                {
                    "id": component_id,
                    "label": component.get("label") or COMPONENT_LABELS[component_id],
                    "version": component.get("version") or self.checkpoint_data.get("version_tag"),
                    "probability_a": round(float(probability), 4),
                    **decision,
                }
            )
        return votes

    def predict(
        self,
        feature_vector: FeatureVector,
        odds_a: Optional[float] = None,
        odds_b: Optional[float] = None,
        min_edge_threshold: float = MIN_COMMIT_EDGE,
        is_shadow: bool = False,
        peer_probabilities: Optional[list] = None,
        segment_reliability: Optional[float] = None,
    ) -> MLPrediction:
        """Current-snapshot view. Confirmation across snapshots stays in the caller."""
        del min_edge_threshold, peer_probabilities, segment_reliability
        market = _feature_market(feature_vector)
        votes = self.component_votes(feature_vector, odds_a, odds_b, market)
        sides = {item.get("side") for item in votes}
        unanimous = len(sides) == 1 and None not in sides
        chosen = next(iter(sides)) if unanimous else None
        agreed = [item for item in votes if item.get("side") == chosen] if chosen else votes
        probability = float(np.mean([item["probability"] for item in agreed])) if agreed else 0.5
        if chosen == "player_b":
            probability_a = round(1.0 - probability, 4)
        elif chosen == "player_a":
            probability_a = round(probability, 4)
        else:
            probability_a = round(float(np.mean([item["probability_a"] for item in votes])), 4)
        probability_b = round(1.0 - probability_a, 4)
        prices = side_odds(feature_vector.features or {})
        price_a = float(odds_a) if odds_a else prices[0]
        price_b = float(odds_b) if odds_b else prices[1]
        outcomes = [
            self._outcome("player_a", probability_a, price_a),
            self._outcome("player_b", probability_b, price_b),
        ]
        best = next((item for item in outcomes if item.outcome == chosen), None) if chosen else None
        return MLPrediction(
            prediction_id=f"pred_{uuid.uuid4().hex[:12]}",
            event_id=feature_vector.event_id,
            sport_code=feature_vector.sport_code,
            model_version=str(self.checkpoint_data.get("version_tag") or "tennis_v1.0.0"),
            market=market,
            outcomes=outcomes,
            best_outcome=best.outcome if best else None,
            has_positive_edge=bool(best),
            is_shadow=is_shadow,
        )

    def _probability(self, component_id: str, component: Dict[str, Any], row: np.ndarray, odds_a: float, odds_b: float, market: str) -> float:
        if component_id == "residual_mlp":
            return self._residual_probability(component, row, odds_a, odds_b, market)
        model = component["model"]
        return float(model.predict_proba(row)[0][1])

    def _residual_probability(self, component: Dict[str, Any], row: np.ndarray, odds_a: float, odds_b: float, market: str) -> float:
        import torch

        network = self._residual_network(component)
        scaler_mean = np.asarray(component["scaler_mean"], dtype=np.float32)
        scaler_scale = np.asarray(component["scaler_scale"], dtype=np.float32)
        scaled = (row.astype(np.float32) - scaler_mean) / np.where(scaler_scale == 0, 1.0, scaler_scale)
        with torch.no_grad():
            raw = network(torch.from_numpy(scaled)).squeeze(-1)
            market_probability = _novig(odds_a, odds_b)
            base = torch.log(
                torch.clamp(torch.tensor(market_probability), 1e-4, 1 - 1e-4)
                / (1 - torch.clamp(torch.tensor(market_probability), 1e-4, 1 - 1e-4))
            )
            logits = base + raw.clamp(-0.35, 0.35)
            probability = float(torch.sigmoid(logits).detach().cpu().numpy().reshape(-1)[0])
        family = "set" if str(market).startswith("set_winner") else "match"
        calibrator = (component.get("calibrators") or {}).get(family)
        if calibrator is not None:
            probability = float(calibrator.transform(np.asarray([probability]))[0])
        return _clip_residual(probability, market_probability)

    def _residual_network(self, component: Dict[str, Any]):
        if self._network is not None:
            return self._network
        from .compare.feature_models import _mlp

        network = _mlp(len(component["scaler_mean"]), tuple(component["widths"]), float(component.get("dropout") or 0.0))
        network.load_state_dict(component["state_dict"])
        network.eval()
        self._network = network
        return network

    @staticmethod
    def _outcome(name: str, probability: float, odds: float) -> OutcomePrediction:
        fair = max(1.01, round(1.0 / max(0.001, probability), 2))
        edge = round((probability * odds) - 1.0, 4) if odds > 1.0 else -1.0
        expected = round(probability * (odds - 1.0) - (1.0 - probability), 4) if odds > 1.0 else 0.0
        return OutcomePrediction(
            outcome=name,
            model_probability=round(probability, 4),
            bookmaker_odds=float(odds or 0.0),
            fair_odds=fair,
            edge=edge,
            expected_value=expected,
            confidence=round(min(1.0, abs(probability - 0.5) * 2.0), 4),
        )


def _feature_market(feature_vector: FeatureVector) -> str:
    try:
        if float((feature_vector.features or {}).get("market_is_set") or 0.0) >= 0.5:
            return "set_winner"
    except (TypeError, ValueError):
        return "match_winner"
    return "match_winner"


def _novig(odds_a: float, odds_b: float) -> float:
    if odds_a > 1.0 and odds_b > 1.0:
        total = 1.0 / odds_a + 1.0 / odds_b
        return (1.0 / odds_a) / total
    return 0.5


def _clip_residual(probability: float, market_probability: float) -> float:
    from .compare.candidates.ensemble import apply_market_offset, logit

    shift = float(logit(np.asarray([probability]))[0] - logit(np.asarray([market_probability]))[0])
    if abs(shift) <= 0.35:
        return probability
    return float(apply_market_offset(np.asarray([market_probability]), np.asarray([np.clip(shift, -0.35, 0.35)]))[0])


def bundle_metadata() -> Dict[str, Any]:
    """Registry fields stored beside the artifact. The lines stay pinned inside the file."""
    return {
        "kind": "composite",
        "display_label": DISPLAY_LABEL,
        "components": [
            {"id": component_id, "label": COMPONENT_LABELS[component_id]}
            for component_id in COMPONENT_ORDER
        ],
    }
