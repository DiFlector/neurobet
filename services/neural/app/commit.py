"""One side choice per match, shared by live inference and training.

A single snapshot with edge >= MIN_COMMIT_EDGE is only a watch.
The side is committed when the next distinct snapshot agrees on that side.
"""

from typing import List, Optional, Sequence, Tuple

from .strategy import stake_gate

MIN_COMMIT_EDGE = 0.02


def side_odds(features: dict) -> Tuple[float, float]:
    """Odds of the market in this row. A set row uses that set's prices."""
    try:
        if float(features.get("market_is_set") or 0.0) >= 0.5:
            odds_a = float(features.get("market_odds_a") or 0.0)
            odds_b = float(features.get("market_odds_b") or 0.0)
            if odds_a <= 1.0 or odds_b <= 1.0:
                return 0.0, 0.0
            return odds_a, odds_b
        if float(features.get("is_missing_odds") or 0.0) >= 0.5:
            return 0.0, 0.0
    except (TypeError, ValueError):
        return 0.0, 0.0
    odds_a = features.get("current_odds_player_a", features.get("current_odds_team_a"))
    odds_b = features.get("current_odds_player_b", features.get("current_odds_team_b"))
    try:
        return float(odds_a or 0.0), float(odds_b or 0.0)
    except (TypeError, ValueError):
        return 0.0, 0.0


def side_odds_from_row(row: Sequence[float], feature_names: Sequence[str]) -> Tuple[float, float]:
    names = list(feature_names)

    def value(*keys: str) -> float:
        for key in keys:
            if key in names:
                try:
                    return float(row[names.index(key)])
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    if value("market_is_set") >= 0.5:
        odds_a, odds_b = value("market_odds_a"), value("market_odds_b")
        if odds_a <= 1.0 or odds_b <= 1.0:
            return 0.0, 0.0
        return odds_a, odds_b
    if "is_missing_odds" in names:
        try:
            if float(row[names.index("is_missing_odds")]) >= 0.5:
                return 0.0, 0.0
        except (TypeError, ValueError):
            return 0.0, 0.0

    return (
        value("current_odds_player_a", "current_odds_team_a"),
        value("current_odds_player_b", "current_odds_team_b"),
    )


def best_edge(prob_a: float, prob_b: float, odds_a: float, odds_b: float) -> float:
    """Largest (model probability * odds - 1) of the two sides. Missing odds cannot commit."""
    edges = []
    if odds_a > 1.0:
        edges.append(prob_a * odds_a - 1.0)
    if odds_b > 1.0:
        edges.append(prob_b * odds_b - 1.0)
    if not edges:
        return -1.0
    return max(edges)


def is_commit(prob_a: float, prob_b: float, odds_a: float, odds_b: float, min_edge: float = MIN_COMMIT_EDGE) -> bool:
    return best_edge(prob_a, prob_b, odds_a, odds_b) >= min_edge


def favored_side(
    prob_a: float,
    prob_b: float,
    odds_a: float,
    odds_b: float,
    min_edge: float = MIN_COMMIT_EDGE,
    market: str = "match_winner",
    sport_code: Optional[str] = None,
) -> Optional[str]:
    """Side 'a' or 'b' when its edge reaches the threshold. None means wait."""
    if odds_a > 1.0:
        edge_a = prob_a * odds_a - 1.0
    else:
        edge_a = -1.0
    if odds_b > 1.0:
        edge_b = prob_b * odds_b - 1.0
    else:
        edge_b = -1.0
    if str(sport_code or "").strip().lower() == "tennis":
        if odds_a > 1.0 and not stake_gate(prob_a, odds_a, market, sport_code)[0]:
            edge_a = -1.0
        if odds_b > 1.0 and not stake_gate(prob_b, odds_b, market, sport_code)[0]:
            edge_b = -1.0
    if max(edge_a, edge_b) < min_edge:
        return None
    return "a" if edge_a >= edge_b else "b"


def confirming_index(
    probabilities: Sequence[Tuple[float, float]],
    odds: Sequence[Tuple[float, float]],
    min_edge: float = MIN_COMMIT_EDGE,
    observed_at: Optional[Sequence] = None,
    min_interval_sec: float = 0.0,
    required: int = 2,
    market: str = "match_winner",
    sport_code: Optional[str] = None,
) -> Optional[int]:
    """Index of the snapshot that repeats the same side often enough.

    Two agreeing snapshots are the default. A positive interval ignores a repeat
    that arrives sooner than that gap, without resetting the side.
    """
    needed = max(2, int(required))
    streak_side: Optional[str] = None
    streak = 0
    last_time = None
    for index, ((prob_a, prob_b), (odds_a, odds_b)) in enumerate(zip(probabilities, odds)):
        side = favored_side(prob_a, prob_b, odds_a, odds_b, min_edge, market, sport_code)
        if not side or side != streak_side:
            streak_side = side
            streak = 1 if side else 0
            last_time = observed_at[index] if side and observed_at is not None else None
            continue
        if observed_at is not None and min_interval_sec > 0 and last_time is not None:
            gap = (observed_at[index] - last_time).total_seconds()
            if gap < min_interval_sec:
                continue
            last_time = observed_at[index]
        streak += 1
        if streak >= needed:
            return index
    return None


def first_commit_index(
    probabilities: Sequence[Tuple[float, float]],
    odds: Sequence[Tuple[float, float]],
    min_edge: float = MIN_COMMIT_EDGE,
) -> Optional[int]:
    """Confirming snapshot. Kept under the old name for callers."""
    return confirming_index(probabilities, odds, min_edge)


def committed_rows(
    matches: Sequence[Tuple[List[List[float]], int]],
    probabilities_for_match,
    feature_names: Sequence[str],
    min_edge: float = MIN_COMMIT_EDGE,
) -> Tuple[List[List[float]], List[int]]:
    """Keep one row per match: the snapshot that confirms the previous side."""
    rows: List[List[float]] = []
    labels: List[int] = []
    for match_rows, label in matches:
        if not match_rows:
            continue
        probs = list(probabilities_for_match(match_rows))
        odds = [side_odds_from_row(row, feature_names) for row in match_rows]
        index = first_commit_index(probs, odds, min_edge)
        if index is None:
            continue
        rows.append(match_rows[index])
        labels.append(label)
    return rows, labels
