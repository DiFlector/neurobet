"""Base feature engineering calculations, rolling window operators, and no-vig probability."""

import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol, Tuple
import uuid

from contracts import FeatureVector


ROLLING_WINDOWS: List[int] = [5, 10, 30, 60, 300]  # Window sizes in seconds


def calculate_odds_delta(current_odds: float, past_odds: Optional[float]) -> float:
    """Computes absolute odds difference: O(t) - O(t - w)."""
    if past_odds is None or past_odds <= 0:
        return 0.0
    return round(current_odds - past_odds, 4)


def calculate_odds_velocity(delta: float, window_seconds: int) -> float:
    """Computes rate of odds change per second."""
    if window_seconds <= 0:
        return 0.0
    return round(delta / float(window_seconds), 6)


def calculate_volatility(odds_values: List[float]) -> float:
    """Computes standard deviation of odds within the rolling window."""
    if len(odds_values) < 2:
        return 0.0
    try:
        return round(statistics.stdev(odds_values), 6)
    except statistics.StatisticsError:
        return 0.0


def calculate_line_drift(delta: float, threshold: float = 0.01) -> float:
    """
    Directional indicator of line movement:
    +1.0: odds lengthening (probability dropping)
    -1.0: odds shortening (probability rising)
     0.0: stable / no drift
    """
    if delta > threshold:
        return 1.0
    elif delta < -threshold:
        return -1.0
    return 0.0


def calculate_no_vig_probabilities(odds_a: float, odds_b: float) -> Tuple[float, float, float]:
    """
    Computes fair no-vig implied probabilities and margin (overround).
    Formula:
      implied_a = 1 / odds_a
      implied_b = 1 / odds_b
      sum = implied_a + implied_b
      no_vig_a = implied_a / sum
      no_vig_b = implied_b / sum
      margin = sum - 1.0
    """
    if odds_a <= 1.0 or odds_b <= 1.0:
        return 0.5, 0.5, 0.0

    imp_a = 1.0 / odds_a
    imp_b = 1.0 / odds_b
    total_implied = imp_a + imp_b

    no_vig_a = round(imp_a / total_implied, 4)
    no_vig_b = round(imp_b / total_implied, 4)
    margin = round(total_implied - 1.0, 4)

    return no_vig_a, no_vig_b, margin


class FeatureBuilder(Protocol):
    """Protocol for sport-specific feature builders."""
    sport_code: str
    feature_version: str

    def build_features(
        self,
        event_id: str | uuid.UUID,
        cutoff_time: datetime,
        event_snapshots: List[Any],
        odds_snapshots: List[Any],
    ) -> FeatureVector:
        """Constructs deterministic FeatureVector strictly up to cutoff_time."""
        ...
