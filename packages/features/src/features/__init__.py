"""Features package for Neurobet: rolling window signals, no-vig probabilities, and point-in-time calculation."""

from .base import (
    FeatureBuilder,
    ROLLING_WINDOWS,
    calculate_line_drift,
    calculate_no_vig_probabilities,
    calculate_odds_delta,
    calculate_odds_velocity,
    calculate_volatility,
)
from .tennis import TennisFeatureBuilder
from .registry import FeatureBuilderRegistry, registry

__all__ = [
    "FeatureBuilder",
    "ROLLING_WINDOWS",
    "calculate_odds_delta",
    "calculate_odds_velocity",
    "calculate_volatility",
    "calculate_line_drift",
    "calculate_no_vig_probabilities",
    "TennisFeatureBuilder",
    "FeatureBuilderRegistry",
    "registry",
]
