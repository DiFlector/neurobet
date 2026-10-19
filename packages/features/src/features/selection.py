"""Tennis feature selection contracts: legacy Variant B and market-offset v4.

Eliminates 50+ noise features from micro-windows while preserving pure match state,
service performance, line macro-drift (60s), and pre-match quality metrics.
"""

from typing import Dict, List, Optional, Sequence, Tuple
import numpy as np

# Selected tennis features (Variant B: 20 features + 1 market anchor)
SELECTED_TENNIS_FEATURES_V2: List[str] = [
    # 0. Market anchor for residual estimation
    "no_vig_prob_player_a",
    # 1. Match state & score physics (highest empirical importance)
    "games_diff",
    "sets_diff",
    "paren_set_1_diff",
    "total_games_current_set",
    "points_diff",
    "is_server_a",
    "is_break_point",
    "is_tiebreak",
    "current_set",
    "favorite_trailing",
    # 2. In-match service performance & score tape
    "tape_serve_won_a",
    "tape_serve_won_b",
    "tape_point_streak",
    "aces_diff",
    "double_faults_diff",
    # 3. Macro market trend (robust 60s window only)
    "odds_delta_player_a_60s",
    "odds_delta_player_b_60s",
    "bookmaker_margin",
    # 4. Pre-match quality & H2H
    "player_win_rate_diff",
    "h2h_win_rate_a",
    # 5. External research & LLM context
    "context_side_a",
]

# Canonical input contract for the tennis market-offset v4 release. The first
# 22 columns retain their established order; the final column identifies the
# market family so the checkpoint can dispatch to its match/set head.
SELECTED_TENNIS_FEATURES_V4: List[str] = [
    *SELECTED_TENNIS_FEATURES_V2,
    "market_is_set",
]


def select_tennis_v4_features(features: Dict[str, float]) -> Dict[str, float]:
    """Return the strict 23-column tennis v4 contract or reject incomplete input."""
    missing = [name for name in SELECTED_TENNIS_FEATURES_V4 if name not in features]
    if missing:
        raise ValueError(f"Tennis v4 feature contract is missing: {', '.join(missing)}")
    return {name: float(features[name]) for name in SELECTED_TENNIS_FEATURES_V4}


def prune_tennis_features(
    features: Dict[str, float],
    target_names: Optional[Sequence[str]] = None,
) -> Dict[str, float]:
    """Extract only the approved subset of features from a raw dictionary."""
    targets = target_names or SELECTED_TENNIS_FEATURES_V2
    return {name: float(features.get(name, 0.0)) for name in targets}


def prune_matrix(
    X: np.ndarray,
    feature_names: Sequence[str],
    target_names: Optional[Sequence[str]] = None,
) -> Tuple[np.ndarray, List[str]]:
    """Filters columns of matrix X to match the selected feature set."""
    targets = list(target_names or SELECTED_TENNIS_FEATURES_V2)
    name_to_idx = {name: i for i, name in enumerate(feature_names)}
    
    indices = []
    available_targets = []
    for name in targets:
        if name in name_to_idx:
            indices.append(name_to_idx[name])
            available_targets.append(name)
            
    if not indices:
        return X, list(feature_names)
        
    X_pruned = np.asarray(X[:, indices], dtype=float)
    return X_pruned, available_targets


def get_market_probability(features: Dict[str, float]) -> float:
    """Extracts or computes the baseline market implied probability for side A."""
    p_a = features.get("no_vig_prob_player_a")
    if p_a is not None and 0.01 <= float(p_a) <= 0.99:
        return float(p_a)
        
    odds_a = features.get("current_odds_player_a") or features.get("market_odds_a")
    odds_b = features.get("current_odds_player_b") or features.get("market_odds_b")
    
    if odds_a and odds_b and float(odds_a) > 1.0 and float(odds_b) > 1.0:
        inv_a = 1.0 / float(odds_a)
        inv_b = 1.0 / float(odds_b)
        total = inv_a + inv_b
        if total > 0:
            return round(inv_a / total, 4)
            
    return 0.5
