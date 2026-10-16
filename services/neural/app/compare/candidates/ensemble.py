"""Shift the market logit by a clipped correction. A free logistic stack is not used."""

import numpy as np

CLIP = 0.35


def logit(prob: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(prob, dtype=float), 1e-4, 1.0 - 1e-4)
    return np.log(clipped / (1.0 - clipped))


def apply_market_offset(market: np.ndarray, correction: np.ndarray) -> np.ndarray:
    """p = sigmoid(logit(market) + clip(correction, -0.35, 0.35))."""
    base = logit(market)
    shift = np.clip(np.asarray(correction, dtype=float), -CLIP, CLIP)
    return 1.0 / (1.0 + np.exp(-(base + shift)))
