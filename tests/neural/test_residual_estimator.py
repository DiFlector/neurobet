"""Unit tests for ResidualMarketEstimator and market-bounded probability adjustments."""

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.models import (
    ResidualMarketEstimator,
    build_gradient_boosting_estimator,
    build_residual_boosting_pipeline,
    logit_np,
    sigmoid_np,
)


def test_residual_estimator_bounds_probability_shift():
    # Synthetic dataset with 20 features
    np.random.seed(42)
    n_samples = 200
    X = np.random.randn(n_samples, 20)
    
    # Feature index 0 is market probability (e.g. 0.50 for all)
    market_col = 0
    X[:, market_col] = 0.50
    y = np.random.choice([0, 1], size=n_samples)

    base = LogisticRegression()
    # Limit offset to 0.25 in logit space
    residual = ResidualMarketEstimator(base_estimator=base, max_offset=0.25, market_feature_index=market_col)
    residual.fit(X, y)

    # Test predictions on extreme feature inputs
    X_extreme = np.ones((10, 20)) * 100.0  # would cause ordinary model to predict ~0.9999
    X_extreme[:, market_col] = 0.50

    probs = residual.predict_proba(X_extreme)
    p_a = probs[:, 1]

    # logit(0.5) = 0.0 -> max shifted logit is +0.25 -> sigmoid(0.25) ~ 0.562
    # Probability must be bounded tightly around 0.562, NOT 0.999!
    assert np.all(p_a <= 0.57)
    assert np.all(p_a >= 0.43)


def test_residual_estimator_with_favorited_market():
    # Market thinks player A is favorite at 0.70
    X = np.random.randn(50, 20)
    X[:, 0] = 0.70
    y = np.array([1] * 40 + [0] * 10)

    base = LogisticRegression()
    residual = ResidualMarketEstimator(base_estimator=base, max_offset=0.25, market_feature_index=0)
    residual.fit(X, y)

    probs = residual.predict_proba(X)
    p_a = probs[:, 1]

    # logit(0.70) = 0.8473
    # Max shift +0.25 -> logit 1.0973 -> sigmoid(1.0973) = 0.7497
    # Min shift -0.25 -> logit 0.5973 -> sigmoid(0.5973) = 0.6450
    assert np.all(p_a <= 0.76)
    assert np.all(p_a >= 0.63)


def test_regularized_boosting_defaults():
    gb = build_gradient_boosting_estimator()
    assert gb.max_depth == 3
    assert gb.min_samples_leaf == 50
    assert gb.l2_regularization == 5.0
    assert gb.learning_rate == 0.02
    assert gb.early_stopping is True


def test_build_residual_boosting_pipeline():
    pipe = build_residual_boosting_pipeline(market_feature_index=0, max_offset=0.25)
    assert pipe.estimator is not None
    assert isinstance(pipe.estimator, ResidualMarketEstimator)
    assert pipe.estimator.max_offset == 0.25


def test_residual_estimator_with_regressor_base():
    np.random.seed(42)
    n_samples = 200
    X = np.random.randn(n_samples, 20)
    market_col = 0
    X[:, market_col] = 0.50
    y = np.random.choice([0, 1], size=n_samples)

    from app.models import build_gradient_boosting_regressor
    base = build_gradient_boosting_regressor(max_iter=20, max_depth=2)
    residual = ResidualMarketEstimator(base_estimator=base, max_offset=0.25, market_feature_index=market_col)
    residual.fit(X, y)

    X_extreme = np.ones((10, 20)) * 100.0
    X_extreme[:, market_col] = 0.50

    probs = residual.predict_proba(X_extreme)
    p_a = probs[:, 1]
    assert np.all(p_a <= 0.57)
    assert np.all(p_a >= 0.43)


if __name__ == "__main__":
    test_residual_estimator_bounds_probability_shift()
    test_residual_estimator_with_favorited_market()
    test_regularized_boosting_defaults()
    test_build_residual_boosting_pipeline()
    test_residual_estimator_with_regressor_base()
    print("test_residual_estimator: ALL PASSED")
