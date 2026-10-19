import numpy as np
from features.selection import (
    SELECTED_TENNIS_FEATURES_V2,
    get_market_probability,
    prune_matrix,
    prune_tennis_features,
)


def test_selected_features_count_and_members():
    assert len(SELECTED_TENNIS_FEATURES_V2) == 22
    # Market anchor
    assert "no_vig_prob_player_a" in SELECTED_TENNIS_FEATURES_V2
    # Core state physics
    assert "games_diff" in SELECTED_TENNIS_FEATURES_V2
    assert "sets_diff" in SELECTED_TENNIS_FEATURES_V2
    assert "paren_set_1_diff" in SELECTED_TENNIS_FEATURES_V2
    assert "points_diff" in SELECTED_TENNIS_FEATURES_V2
    assert "favorite_trailing" in SELECTED_TENNIS_FEATURES_V2
    # Service tape
    assert "tape_serve_won_a" in SELECTED_TENNIS_FEATURES_V2
    assert "tape_serve_won_b" in SELECTED_TENNIS_FEATURES_V2
    # Macro trend
    assert "odds_delta_player_a_60s" in SELECTED_TENNIS_FEATURES_V2
    assert "odds_delta_player_b_60s" in SELECTED_TENNIS_FEATURES_V2
    # Noise features must NOT be in the features
    assert "odds_delta_player_a_5s" not in SELECTED_TENNIS_FEATURES_V2
    assert "odds_velocity_player_a_5s" not in SELECTED_TENNIS_FEATURES_V2
    assert "line_drift_player_a_5s" not in SELECTED_TENNIS_FEATURES_V2
    assert "odds_volatility_player_a_300s" not in SELECTED_TENNIS_FEATURES_V2
    assert "current_odds_player_a" not in SELECTED_TENNIS_FEATURES_V2


def test_prune_tennis_features_dict():
    raw_dict = {f"feature_{i}": float(i) for i in range(103)}
    raw_dict["no_vig_prob_player_a"] = 0.55
    raw_dict["games_diff"] = 2.0
    raw_dict["sets_diff"] = 1.0
    raw_dict["favorite_trailing"] = 0.4
    raw_dict["tape_serve_won_a"] = 14.0
    raw_dict["odds_delta_player_a_60s"] = 0.05
    raw_dict["context_side_a"] = 1.0

    pruned = prune_tennis_features(raw_dict)
    assert len(pruned) == 22
    assert pruned["no_vig_prob_player_a"] == 0.55
    assert pruned["games_diff"] == 2.0
    assert pruned["sets_diff"] == 1.0
    assert pruned["favorite_trailing"] == 0.4
    assert pruned["tape_serve_won_a"] == 14.0
    assert pruned["odds_delta_player_a_60s"] == 0.05
    assert pruned["context_side_a"] == 1.0
    assert "feature_0" not in pruned


def test_prune_matrix():
    names = [f"f_{i}" for i in range(103)]
    names[5] = "games_diff"
    names[12] = "sets_diff"
    names[25] = "tape_serve_won_a"

    X = np.ones((50, 103))
    X[:, 5] = 3.0
    X[:, 12] = 2.0
    X[:, 25] = 10.0

    target_subset = ["games_diff", "sets_diff", "tape_serve_won_a"]
    X_pruned, pruned_names = prune_matrix(X, names, target_subset)

    assert X_pruned.shape == (50, 3)
    assert pruned_names == target_subset
    assert np.all(X_pruned[:, 0] == 3.0)
    assert np.all(X_pruned[:, 1] == 2.0)
    assert np.all(X_pruned[:, 2] == 10.0)


def test_get_market_probability():
    # From direct no_vig_prob
    assert get_market_probability({"no_vig_prob_player_a": 0.65}) == 0.65
    
    # From current odds
    prob = get_market_probability({"current_odds_player_a": 1.50, "current_odds_player_b": 3.00})
    # inv_a = 1/1.5 = 0.6667, inv_b = 1/3 = 0.3333 -> 0.6667 / 1.0 = 0.6667
    assert 0.66 <= prob <= 0.67

    # Default fallback
    assert get_market_probability({}) == 0.5


if __name__ == "__main__":
    test_selected_features_count_and_members()
    test_prune_tennis_features_dict()
    test_prune_matrix()
    test_get_market_probability()
    print("test_feature_pruning: ALL PASSED")
