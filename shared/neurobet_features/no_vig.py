"""No-vig (fair) implied probability from bookmaker overround."""
from __future__ import annotations

from typing import Optional


def no_vig_probability(coefficient: float, overround: Optional[float]) -> float:
    """Raw implied prob de-vigged by sibling overround. Falls back to raw 1/coeff."""
    if coefficient is None or coefficient <= 1.0:
        return 0.5
    raw = min(max(1.0 / float(coefficient), 0.01), 0.99)
    if overround is None or overround <= 1.0:
        return raw
    return min(max(raw / float(overround), 0.01), 0.99)
