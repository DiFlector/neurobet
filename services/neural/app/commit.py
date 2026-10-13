"""One side choice per match, shared by live inference and training.

A side is committed only when its edge is at least MIN_COMMIT_EDGE.
Later snapshots of the same match are not new choices.
"""

from typing import List, Optional, Sequence, Tuple

MIN_COMMIT_EDGE = 0.02


def side_odds(features: dict) -> Tuple[float, float]:
    """Match-winner odds for side A and side B from a feature dict."""
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


def first_commit_index(
    probabilities: Sequence[Tuple[float, float]],
    odds: Sequence[Tuple[float, float]],
    min_edge: float = MIN_COMMIT_EDGE,
) -> Optional[int]:
    """Index of the first snapshot that commits a side. None if the match never qualifies."""
    for index, ((prob_a, prob_b), (odds_a, odds_b)) in enumerate(zip(probabilities, odds)):
        if is_commit(prob_a, prob_b, odds_a, odds_b, min_edge):
            return index
    return None


def committed_rows(
    matches: Sequence[Tuple[List[List[float]], int]],
    probabilities_for_match,
    feature_names: Sequence[str],
    min_edge: float = MIN_COMMIT_EDGE,
) -> Tuple[List[List[float]], List[int]]:
    """Keep one row per match: the first snapshot whose model edge reaches the commit threshold."""
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
