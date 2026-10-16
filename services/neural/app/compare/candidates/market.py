"""Bookmaker probability with the margin removed. This is the baseline to beat."""

from typing import Sequence

import numpy as np

from ..rows import CompareRow


class MarketCandidate:
    name = "A_market"

    def fit(self, rows: Sequence[CompareRow]) -> None:
        return None

    def predict(self, rows: Sequence[CompareRow]) -> np.ndarray:
        return np.asarray([row.market_prob_a for row in rows], dtype=float)
