"""Residual models. They move the no-vig price by a small logit shift and never replace it."""

from typing import Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ..rows import CompareRow
from .ensemble import apply_market_offset, logit
from .markov import MarkovCandidate

SURFACES = ("hard", "clay", "grass", "carpet")
LEVELS = ("atp_wta", "challenger", "itf")
ROUNDS = ("f", "sf", "qf", "r16", "r32", "rr")


def _round_token(value: str) -> str:
    text = (value or "").lower().replace(" ", "")
    if text in {"final", "thefinal"}:
        return "f"
    if text in {"semifinal", "semifinals"}:
        return "sf"
    if text in {"quarterfinal", "quarterfinals"}:
        return "qf"
    if text in ROUNDS:
        return text
    return ""


def residual_vector(row: CompareRow, markov_delta: float) -> list:
    """Features that do not include the market probability itself."""
    closed_sets = float(row.sets_a + row.sets_b)
    break_side = float((row.games_a > row.games_b) - (row.games_b > row.games_a))
    values = [
        float(row.elo_diff) / 400.0,
        float(row.surface_elo_diff) / 400.0,
        float(row.glicko_diff) / 400.0,
        float(row.glicko_rd) / 350.0,
        float(row.serve_rate_diff),
        float(row.rank_diff),
        float(row.best_of),
        float(row.sets_a - row.sets_b),
        float(row.games_a - row.games_b),
        float(row.points_a - row.points_b) / 4.0,
        1.0 if row.server == "a" else 0.0,
        1.0 if row.phase == "live" else 0.0,
        closed_sets,
        break_side,
        float(markov_delta),
    ]
    for name in SURFACES:
        values.append(1.0 if row.surface == name else 0.0)
    for name in LEVELS:
        values.append(1.0 if row.tour_level == name else 0.0)
    token = _round_token(row.round_name)
    for name in ROUNDS:
        values.append(1.0 if token == name else 0.0)
    return values


def _weights(rows: Sequence[CompareRow]) -> np.ndarray:
    counts = {}
    for row in rows:
        counts[row.event_id] = counts.get(row.event_id, 0) + 1
    return np.asarray([1.0 / counts[row.event_id] for row in rows], dtype=float)


def markov_delta(rows: Sequence[CompareRow], markov_probs: np.ndarray) -> np.ndarray:
    market = np.asarray([row.market_prob_a for row in rows], dtype=float)
    return logit(markov_probs) - logit(market)


def _masked(vector: list, kind: str) -> list:
    if kind == "elo":
        keep = set(range(0, 6))
    elif kind == "score":
        keep = set(range(6, 13))
    else:
        return vector
    return [value if index in keep else 0.0 for index, value in enumerate(vector)]


class ResidualCandidate:
    def __init__(self, kind: str = "logistic"):
        self.kind = kind
        self.name = "R_logistic" if kind == "logistic" else "R_hist_gradient_boosting"
        self.model = None
        self.insufficient = False
        self.fit_matches = 0

    def fit(self, rows: Sequence[CompareRow], markov_probs: np.ndarray) -> None:
        labels = np.asarray([row.label for row in rows], dtype=int)
        self.fit_matches = len({row.event_id for row in rows})
        if self.fit_matches < 40 or len(set(labels.tolist())) < 2:
            self.model = None
            self.insufficient = True
            return
        self.insufficient = False
        matrix = np.asarray(
            [residual_vector(row, float(delta)) for row, delta in zip(rows, markov_delta(rows, markov_probs))],
            dtype=float,
        )
        weights = _weights(rows)
        if self.kind == "logistic":
            model = Pipeline(
                [
                    ("scaler", StandardScaler()),
                    ("classifier", LogisticRegression(C=1.0, max_iter=500, solver="lbfgs")),
                ]
            )
            model.fit(matrix, labels, classifier__sample_weight=weights)
        else:
            model = HistGradientBoostingClassifier(
                learning_rate=0.05,
                max_iter=80,
                max_depth=3,
                min_samples_leaf=20,
                l2_regularization=1.0,
            )
            model.fit(matrix, labels, sample_weight=weights)
        self.model = model

    def predict(self, rows: Sequence[CompareRow], markov_probs: np.ndarray) -> np.ndarray:
        market = np.asarray([row.market_prob_a for row in rows], dtype=float)
        if self.model is None or not rows:
            return market
        matrix = np.asarray(
            [residual_vector(row, float(delta)) for row, delta in zip(rows, markov_delta(rows, markov_probs))],
            dtype=float,
        )
        modelled = self.model.predict_proba(matrix)[:, 1]
        correction = logit(modelled) - logit(market)
        shifted = apply_market_offset(market, correction)
        return np.where(np.isfinite(shifted), shifted, market)


class ResidualAblation(ResidualCandidate):
    """Market baseline, or a residual that may use only Elo or only the score tape."""

    def __init__(self, family: str = "full"):
        super().__init__("logistic")
        self.family = family
        self.name = f"R_{family}"

    def fit(self, rows: Sequence[CompareRow], markov_probs: np.ndarray) -> None:
        if self.family == "market":
            self.model = None
            self.insufficient = False
            self.fit_matches = len({row.event_id for row in rows})
            return
        super().fit(rows, markov_probs)

    def predict(self, rows: Sequence[CompareRow], markov_probs: np.ndarray) -> np.ndarray:
        if self.family == "market" or self.model is None:
            self.insufficient = self.family != "market" and self.model is None
            return np.asarray([row.market_prob_a for row in rows], dtype=float)
        market = np.asarray([row.market_prob_a for row in rows], dtype=float)
        matrix = np.asarray(
            [
                _masked(residual_vector(row, float(delta)), self.family)
                for row, delta in zip(rows, markov_delta(rows, markov_probs))
            ],
            dtype=float,
        )
        modelled = self.model.predict_proba(matrix)[:, 1]
        correction = logit(modelled) - logit(market)
        shifted = apply_market_offset(market, correction)
        return np.where(np.isfinite(shifted), shifted, market)


def markov_probabilities(rows: Sequence[CompareRow]) -> np.ndarray:
    return MarkovCandidate().predict(rows)
