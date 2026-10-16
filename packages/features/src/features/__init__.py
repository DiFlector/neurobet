"""Features package for Neurobet: rolling window signals, no-vig probabilities, and point-in-time calculation."""

from .base import (
    FeatureBuilder,
    ROLLING_WINDOWS,
    calculate_line_drift,
    calculate_no_vig_probabilities,
    last_quoted_odds,
    calculate_odds_delta,
    calculate_odds_velocity,
    calculate_volatility,
)
from .tennis import TennisFeatureBuilder
from .basketball import BasketballFeatureBuilder
from .volleyball import VolleyballFeatureBuilder
from .hockey import HockeyFeatureBuilder
from .football import FootballFeatureBuilder
from .registry import FeatureBuilderRegistry, registry
from .leakage import DataLeakageDetectedError, DataLeakageDetector

__all__ = [
    "FeatureBuilder",
    "ROLLING_WINDOWS",
    "calculate_odds_delta",
    "calculate_odds_velocity",
    "calculate_volatility",
    "calculate_line_drift",
    "calculate_no_vig_probabilities",
    "last_quoted_odds",
    "TennisFeatureBuilder",
    "BasketballFeatureBuilder",
    "VolleyballFeatureBuilder",
    "HockeyFeatureBuilder",
    "FootballFeatureBuilder",
    "FeatureBuilderRegistry",
    "registry",
    "DataLeakageDetectedError",
    "DataLeakageDetector",
]

