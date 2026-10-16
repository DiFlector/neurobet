"""Hierarchical Markov match probability from the score and two serve-point probabilities."""

from functools import lru_cache
from typing import Sequence

from ..rows import CompareRow


def _clip(value: float) -> float:
    return min(0.9, max(0.35, value))


def game_win(point_a: int, point_b: int, p: float) -> float:
    """Probability the server wins the game from a point score. Points are 0..3, 4 is advantage."""
    q = 1.0 - p

    def from_deuce() -> float:
        denom = p * p + q * q
        if denom <= 0:
            return 0.5
        return (p * p) / denom

    if point_a >= 3 and point_b >= 3:
        if point_a == point_b:
            return from_deuce()
        if point_a == point_b + 1:
            return p + q * from_deuce()
        if point_b == point_a + 1:
            return p * from_deuce()
        return 1.0 if point_a > point_b else 0.0

    @lru_cache(maxsize=None)
    def rec(a: int, b: int) -> float:
        if a >= 4 and a - b >= 2:
            return 1.0
        if b >= 4 and b - a >= 2:
            return 0.0
        if a >= 3 and b >= 3:
            return from_deuce() if a == b else (p + q * from_deuce() if a > b else p * from_deuce())
        return p * rec(a + 1, b) + q * rec(a, b + 1)

    return rec(point_a, point_b)


def tiebreak_win(points_a: int, points_b: int, p_server_a: float, p_server_b: float, server_a: bool) -> float:
    """First to 7 by 2. Serve changes after the first point and then every two points."""

    @lru_cache(maxsize=None)
    def rec(a: int, b: int, serve_a: bool, served: int) -> float:
        if (a >= 7 or b >= 7) and abs(a - b) >= 2:
            return 1.0 if a > b else 0.0
        if a > 30 or b > 30:
            return 1.0 if a > b else 0.0
        p = p_server_a if serve_a else (1.0 - p_server_b)
        nxt = served + 1
        switch = nxt == 1 or (nxt > 1 and (nxt - 1) % 2 == 0)
        return p * rec(a + 1, b, (not serve_a) if switch else serve_a, nxt) + (1.0 - p) * rec(
            a, b + 1, (not serve_a) if switch else serve_a, nxt
        )

    return rec(points_a, points_b, server_a, points_a + points_b)


def match_win(
    sets_a: int,
    sets_b: int,
    games_a: int,
    games_b: int,
    points_a: int,
    points_b: int,
    p_a: float,
    p_b: float,
    server: str,
    sets_to_win: int = 2,
    tiebreak: bool = False,
) -> float:
    """Probability player A wins the match from the current score."""
    if sets_a >= sets_to_win:
        return 0.98
    if sets_b >= sets_to_win:
        return 0.02
    p_a = _clip(p_a)
    p_b = _clip(p_b)
    server_a = server == "a"

    @lru_cache(maxsize=None)
    def from_set(sa: int, sb: int, ga: int, gb: int, serve_a: bool, pa: int, pb: int, in_tb: bool) -> float:
        if sa >= sets_to_win:
            return 1.0
        if sb >= sets_to_win:
            return 0.0
        if in_tb or (ga == 6 and gb == 6):
            tb = tiebreak_win(pa, pb, p_a, p_b, serve_a)
            return tb * from_set(sa + 1, sb, 0, 0, not serve_a, 0, 0, False) + (1.0 - tb) * from_set(
                sa, sb + 1, 0, 0, not serve_a, 0, 0, False
            )
        if ga >= 6 and ga - gb >= 2:
            return from_set(sa + 1, sb, 0, 0, not serve_a, 0, 0, False)
        if gb >= 6 and gb - ga >= 2:
            return from_set(sa, sb + 1, 0, 0, not serve_a, 0, 0, False)
        hold = game_win(pa, pb, p_a if serve_a else p_b)
        if serve_a:
            return hold * from_set(sa, sb, ga + 1, gb, False, 0, 0, False) + (1.0 - hold) * from_set(
                sa, sb, ga, gb + 1, False, 0, 0, False
            )
        # B is serving. hold is P(B wins the game) = game_win(..., p_b).
        return hold * from_set(sa, sb, ga, gb + 1, True, 0, 0, False) + (1.0 - hold) * from_set(
            sa, sb, ga + 1, gb, True, 0, 0, False
        )

    if not server:
        left = from_set(sets_a, sets_b, games_a, games_b, True, points_a, points_b, tiebreak)
        from_set.cache_clear()
        right = from_set(sets_a, sets_b, games_a, games_b, False, points_a, points_b, tiebreak)
        estimate = 0.5 * (left + right)
    else:
        estimate = from_set(sets_a, sets_b, games_a, games_b, server_a, points_a, points_b, tiebreak)
    return min(0.98, max(0.02, estimate))


@lru_cache(maxsize=4096)
def _start_probability(p_a_millis: int, p_b_millis: int, sets_to_win: int) -> float:
    return match_win(0, 0, 0, 0, 0, 0, p_a_millis / 1000.0, p_b_millis / 1000.0, "", sets_to_win=sets_to_win)


def serve_points_for_match_prob(market_prob: float, sets_to_win: int) -> tuple:
    """Invert a prematch market probability into two point-on-serve probabilities."""
    target = min(0.97, max(0.03, market_prob))
    low, high = -0.22, 0.22
    best = (0.62, 0.62)
    target_sets = 3 if sets_to_win >= 3 else 2
    for _ in range(16):
        mid = (low + high) / 2.0
        p_a, p_b = _clip(0.62 + mid), _clip(0.62 - mid)
        estimate = _start_probability(int(round(p_a * 1000)), int(round(p_b * 1000)), target_sets)
        best = (p_a, p_b)
        if estimate < target:
            low = mid
        else:
            high = mid
    return best


class MarkovCandidate:
    name = "E_markov"

    def fit(self, rows: Sequence[CompareRow]) -> None:
        return None

    def predict(self, rows: Sequence[CompareRow]) -> "np.ndarray":
        import numpy as np

        out = np.empty(len(rows), dtype=float)
        for index, row in enumerate(rows):
            p_a, p_b = serve_points_for_match_prob(row.market_prob_a, row.sets_to_win)
            hist_a = 0.5 + row.serve_rate_diff / 2.0
            hist_b = 0.5 - row.serve_rate_diff / 2.0
            if abs(row.serve_rate_diff) > 0:
                p_a = _clip(0.7 * p_a + 0.3 * (0.62 + (hist_a - 0.5)))
                p_b = _clip(0.7 * p_b + 0.3 * (0.62 + (hist_b - 0.5)))
            out[index] = match_win(
                row.sets_a,
                row.sets_b,
                row.games_a,
                row.games_b,
                row.points_a,
                row.points_b,
                p_a,
                p_b,
                row.server,
                sets_to_win=row.sets_to_win,
                tiebreak=row.games_a >= 6 and row.games_b >= 6,
            )
        return out
