"""One priced observation. Every snapshot of a match shares event_id."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class CompareRow:
    event_id: str
    observed_at: datetime
    started_at: datetime
    finished_at: datetime
    phase: str
    label: int
    odds_a: float
    odds_b: float
    market_prob_a: float
    surface: str = "unknown"
    tour_level: str = "unknown"
    round_name: str = ""
    best_of: int = 3
    gender: str = "unknown"
    player_a: str = ""
    player_b: str = ""
    sets_a: int = 0
    sets_b: int = 0
    games_a: int = 0
    games_b: int = 0
    points_a: int = 0
    points_b: int = 0
    server: str = ""
    sets_to_win: int = 2
    suspended: bool = False
    elo_diff: float = 0.0
    surface_elo_diff: float = 0.0
    glicko_diff: float = 0.0
    glicko_rd: float = 350.0
    serve_rate_diff: float = 0.0
    rank_diff: float = 0.0


def no_vig(odds_a: float, odds_b: float) -> float:
    if odds_a <= 1.0 or odds_b <= 1.0:
        return 0.5
    implied_a = 1.0 / odds_a
    implied_b = 1.0 / odds_b
    total = implied_a + implied_b
    if total <= 0:
        return 0.5
    return implied_a / total


FEATURE_NAMES = (
    "market_prob_a",
    "elo_diff",
    "surface_elo_diff",
    "glicko_diff",
    "glicko_rd",
    "serve_rate_diff",
    "rank_diff",
    "sets_diff",
    "games_diff",
    "points_diff",
    "server_a",
    "is_live",
    "best_of",
)


def feature_vector(row: CompareRow) -> list:
    return [
        float(row.market_prob_a),
        float(row.elo_diff) / 400.0,
        float(row.surface_elo_diff) / 400.0,
        float(row.glicko_diff) / 400.0,
        float(row.glicko_rd) / 350.0,
        float(row.serve_rate_diff),
        float(row.rank_diff),
        float(row.sets_a - row.sets_b),
        float(row.games_a - row.games_b),
        float(row.points_a - row.points_b) / 4.0,
        1.0 if row.server == "a" else 0.0,
        1.0 if row.phase == "live" else 0.0,
        float(row.best_of),
    ]
