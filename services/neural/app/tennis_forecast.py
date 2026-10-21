"""Odds-free tennis forecasts and legal short-horizon score scenarios."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple


POINTS = {"0": 0, "15": 1, "30": 2, "40": 3, "A": 4, "AD": 4, "ADV": 4}
ODDS_FREE_FEATURE_VERSION = "tennis_odds_free_v1"
ODDS_FREE_FEATURE_SOURCE_GROUPS = {
    "player_metrics": (
        "elo_a", "elo_b", "elo_diff", "win_rate_90d_a", "win_rate_90d_b",
        "win_rate_90d_diff", "match_count_a", "match_count_b",
        "player_metrics_missing_a", "player_metrics_missing_b",
    ),
    "serve_metrics_a": ("serve_probability_a", "serve_sample_weight_a"),
    "serve_metrics_b": ("serve_probability_b", "serve_sample_weight_b"),
    "event_state": (
        "sets_a", "sets_b", "games_a", "games_b", "points_a", "points_b",
        "server_a", "server_b", "server_unknown", "is_tiebreak", "sets_to_win",
    ),
    "event_surface": ("surface_hard", "surface_clay", "surface_grass", "surface_carpet", "surface_unknown"),
}
ODDS_FREE_FEATURE_NAMES = (
    "elo_a", "elo_b", "elo_diff",
    "win_rate_90d_a", "win_rate_90d_b", "win_rate_90d_diff",
    "match_count_a", "match_count_b",
    "player_metrics_missing_a", "player_metrics_missing_b",
    "serve_probability_a", "serve_probability_b",
    "serve_sample_weight_a", "serve_sample_weight_b",
    "sets_a", "sets_b", "games_a", "games_b", "points_a", "points_b",
    "server_a", "server_b", "server_unknown", "is_tiebreak", "sets_to_win",
    "surface_hard", "surface_clay", "surface_grass", "surface_carpet", "surface_unknown",
)


def validate_odds_free_feature_availability(
    as_of: datetime, source_available_at: Dict[str, Any]
) -> Dict[str, Any]:
    """Fail closed when a sports feature source is missing or arrived after a slice."""
    cutoff = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)
    missing, future = [], []
    for source_group in ODDS_FREE_FEATURE_SOURCE_GROUPS:
        raw = source_available_at.get(source_group)
        if isinstance(raw, str):
            try:
                raw = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                raw = None
        if not isinstance(raw, datetime):
            missing.append(source_group)
            continue
        available = raw.replace(tzinfo=timezone.utc) if raw.tzinfo is None else raw.astimezone(timezone.utc)
        if available > cutoff:
            future.append(source_group)
    return {
        "certified": not missing and not future,
        "missing_sources": missing,
        "future_sources": future,
        "as_of": cutoff.isoformat(),
    }


@dataclass(frozen=True)
class TennisState:
    sets_a: int = 0
    sets_b: int = 0
    games_a: int = 0
    games_b: int = 0
    points_a: int = 0
    points_b: int = 0
    server: Optional[str] = None
    tiebreak: bool = False
    sets_to_win: int = 2
    tiebreak_server: Optional[str] = None


def build_odds_free_feature_vector(
    state: TennisState,
    pair: Dict[str, Any],
    serve_a: Tuple[float, float, float],
    serve_b: Tuple[float, float, float],
    surface: str = "unknown",
) -> Tuple[float, ...]:
    """Return the closed, ordered sports-only input contract for future candidates."""
    surface_key = str(surface or "unknown").strip().casefold()
    surface_key = surface_key if surface_key in {"hard", "clay", "grass", "carpet"} else "unknown"
    values = {
        "elo_a": pair.get("elo_a", 1500.0), "elo_b": pair.get("elo_b", 1500.0),
        "elo_diff": pair.get("elo_diff", 0.0),
        "win_rate_90d_a": pair.get("win_rate_90d_a", 0.5),
        "win_rate_90d_b": pair.get("win_rate_90d_b", 0.5),
        "win_rate_90d_diff": pair.get("win_rate_90d_diff", 0.0),
        "match_count_a": pair.get("match_count_a", 0), "match_count_b": pair.get("match_count_b", 0),
        "player_metrics_missing_a": pair.get("player_metrics_missing_a", True),
        "player_metrics_missing_b": pair.get("player_metrics_missing_b", True),
        "serve_probability_a": serve_a[0], "serve_probability_b": serve_b[0],
        "serve_sample_weight_a": serve_a[2], "serve_sample_weight_b": serve_b[2],
        "sets_a": state.sets_a, "sets_b": state.sets_b,
        "games_a": state.games_a, "games_b": state.games_b,
        "points_a": state.points_a, "points_b": state.points_b,
        "server_a": state.server == "a", "server_b": state.server == "b",
        "server_unknown": state.server not in {"a", "b"},
        "is_tiebreak": state.tiebreak, "sets_to_win": state.sets_to_win,
        "surface_hard": surface_key == "hard", "surface_clay": surface_key == "clay",
        "surface_grass": surface_key == "grass", "surface_carpet": surface_key == "carpet",
        "surface_unknown": surface_key == "unknown",
    }
    vector = tuple(float(bool(values[name])) if isinstance(values[name], bool) else float(values[name])
                   for name in ODDS_FREE_FEATURE_NAMES)
    if len(vector) != len(ODDS_FREE_FEATURE_NAMES) or any(not math.isfinite(value) for value in vector):
        raise ValueError("invalid_odds_free_feature_vector")
    return vector


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_tennis_state(detail: Any, sport_state: Any = None, best_of: int = 3) -> Tuple[Optional[TennisState], Optional[str]]:
    """Parse supported Fonbet score snapshots. Unknown states fail closed."""
    from tennis_adapter.point_history import is_legal_point_state
    best_of = best_of if best_of in (3, 5) else 3
    detail = detail if isinstance(detail, dict) else {}
    sport_state = sport_state if isinstance(sport_state, dict) else {}
    stats = sport_state.get("stats") if isinstance(sport_state.get("stats"), dict) else detail.get("stats", {})
    # Fonbet adapter stores the live set line under stats.set_lines and closed
    # set results under detail.sets. Prefer the live line to avoid treating the
    # last completed set as the current score.
    sets = stats.get("set_lines") or detail.get("set_lines") or detail.get("sets") or (sport_state.get("sets") if isinstance(sport_state.get("sets"), list) else [])
    current = next((row for row in reversed(sets) if isinstance(row, dict) and row.get("live")), None)
    if current is None and not stats.get("set_lines") and sets and isinstance(sets[-1], dict):
        current = sets[-1]
    games_a_raw = (current or {}).get("games_a", detail.get("current_game_a"))
    games_b_raw = (current or {}).get("games_b", detail.get("current_game_b"))
    if games_a_raw is None or games_b_raw is None:
        return None, "GAMES_SCORE_MISSING"
    games_a, games_b = _int(games_a_raw, -1), _int(games_b_raw, -1)
    sets_a = _int(stats.get("sets_a", detail.get("sets_a", 0)))
    sets_b = _int(stats.get("sets_b", detail.get("sets_b", 0)))
    game = detail.get("current_game") if isinstance(detail.get("current_game"), dict) else {}
    is_tiebreak = bool(
        detail.get("tiebreak")
        or (game.get("is_tiebreak") if isinstance(game, dict) else False)
        or sport_state.get("tiebreak")
        or (games_a == 6 and games_b == 6)
    )
    raw_points_a = str(game.get("points_a", detail.get("points_a", "0"))).upper()
    raw_points_b = str(game.get("points_b", detail.get("points_b", "0"))).upper()
    if is_tiebreak:
        try:
            points_a, points_b = int(raw_points_a), int(raw_points_b)
        except ValueError:
            points_a = points_b = None
    else:
        points_a, points_b = POINTS.get(raw_points_a), POINTS.get(raw_points_b)
    if (
        points_a is None or points_b is None or games_a > 7 or games_b > 7
        or min(sets_a, sets_b) < 0 or max(sets_a, sets_b) >= best_of // 2 + 1
        or (games_a == games_b == 7)
        or not is_legal_point_state(points_a, points_b, tie_break=is_tiebreak)
    ):
        return None, "UNSUPPORTED_SCORE"
    server = sport_state.get("server") or detail.get("server")
    server = {"player_a": "a", "player_b": "b", "1": "a", "2": "b"}.get(str(server).lower())
    tiebreak_server = sport_state.get("tiebreak_server") or detail.get("tiebreak_server")
    tiebreak_server = {"player_a": "a", "player_b": "b", "1": "a", "2": "b"}.get(str(tiebreak_server).lower())
    return TennisState(
        sets_a, sets_b, games_a, games_b, points_a, points_b, server,
        is_tiebreak,
        best_of // 2 + 1,
        tiebreak_server,
    ), None


def _point_probability(state: TennisState, p_serve_a: float, p_serve_b: float) -> float:
    """P(A wins the next point); serve rates are independent of bookmaker data."""
    if state.server == "a":
        return p_serve_a
    if state.server == "b":
        return 1.0 - p_serve_b
    # Unknown server: average the legal serve assignment rather than guessing.
    return 0.5 * (p_serve_a + 1.0 - p_serve_b)


def _advance_point(state: TennisState, winner: str) -> TennisState:
    if state.sets_a >= state.sets_to_win or state.sets_b >= state.sets_to_win:
        return state
    pa, pb = state.points_a, state.points_b
    if state.tiebreak:
        pa += winner == "a"
        pb += winner == "b"
        if max(pa, pb) >= 7 and abs(pa - pb) >= 2:
            set_winner = "a" if pa > pb else "b"
            next_set_server = _next_server(state.tiebreak_server or state.server)
            state = _close_set(state, set_winner)
            return TennisState(**{**asdict(state), "server": next_set_server, "tiebreak": False, "tiebreak_server": None})
        first_server = state.tiebreak_server or state.server
        played = pa + pb
        switches = 0 if played == 0 else 1 + (played - 1) // 2
        next_server = first_server if switches % 2 == 0 else _next_server(first_server)
        return TennisState(**{**asdict(state), "points_a": pa, "points_b": pb, "server": next_server, "tiebreak_server": first_server})

    pa += winner == "a"
    pb += winner == "b"
    game_winner = None
    if max(pa, pb) >= 4 and abs(pa - pb) >= 2:
        game_winner = "a" if pa > pb else "b"
    if game_winner:
        ga = state.games_a + (game_winner == "a")
        gb = state.games_b + (game_winner == "b")
        set_winner = None
        if max(ga, gb) >= 6 and abs(ga - gb) >= 2:
            set_winner = "a" if ga > gb else "b"
        elif (ga, gb) in ((7, 6), (6, 7)):
            set_winner = "a" if ga > gb else "b"
        if set_winner:
            return TennisState(**{**asdict(_close_set(TennisState(**{**asdict(state), "games_a": ga, "games_b": gb}), set_winner)), "server": _next_server(state.server)})
        tie = ga == 6 and gb == 6
        next_server = _next_server(state.server)
        return TennisState(**{**asdict(state), "games_a": ga, "games_b": gb, "points_a": 0, "points_b": 0, "server": next_server, "tiebreak": tie, "tiebreak_server": next_server if tie else None})
    # Keep deuce/advantage in a bounded representation to avoid a 4:4 <-> 5:4 cycle.
    if pa >= 3 and pb >= 3:
        if pa == pb:
            pa = pb = 3
        elif pa > pb:
            pa, pb = 4, 3
        else:
            pa, pb = 3, 4
    return TennisState(**{**asdict(state), "points_a": pa, "points_b": pb})


def _close_set(state: TennisState, winner: str) -> TennisState:
    a = state.sets_a + (winner == "a")
    b = state.sets_b + (winner == "b")
    return TennisState(a, b, 0, 0, 0, 0, state.server, False, state.sets_to_win, None)


def _set_game_winner(state: TennisState, winner: str) -> TennisState:
    ga = state.games_a + (winner == "a")
    gb = state.games_b + (winner == "b")
    if (max(ga, gb) >= 6 and abs(ga - gb) >= 2) or (max(ga, gb) == 7 and min(ga, gb) == 6):
        closed = _close_set(TennisState(**{**asdict(state), "games_a": ga, "games_b": gb}), "a" if ga > gb else "b")
        return TennisState(**{**asdict(closed), "server": _next_server(state.tiebreak_server or state.server)})
    tie = ga == gb == 6
    server = _next_server(state.server)
    return TennisState(**{**asdict(state), "games_a": ga, "games_b": gb, "points_a": 0, "points_b": 0, "server": server, "tiebreak": tie, "tiebreak_server": server if tie else None})


@lru_cache(maxsize=300_000)
def _game_win(pa: int, pb: int, p: float) -> float:
    if pa >= 4 and pa - pb >= 2:
        return 1.0
    if pb >= 4 and pb - pa >= 2:
        return 0.0
    if pa >= 3 and pb >= 3:
        # Reduce any advantage score to deuce before recursing.
        if pa == pb:
            q = 1.0 - p
            return p * p / (p * p + q * q)
        if pa > pb:
            return p + (1.0 - p) * _game_win(3, 3, p)
        return p * _game_win(3, 3, p)
    return p * _game_win(pa + 1, pb, p) + (1.0 - p) * _game_win(pa, pb + 1, p)


def _point_prob(state: TennisState, p_a: float, p_b: float) -> float:
    return _point_probability(state, p_a, p_b)


@lru_cache(maxsize=300_000)
def _match_win(state: TennisState, p_a_milli: int, p_b_milli: int) -> float:
    pa, pb = p_a_milli / 1000.0, p_b_milli / 1000.0
    if state.sets_a >= state.sets_to_win:
        return 1.0
    if state.sets_b >= state.sets_to_win:
        return 0.0
    if state.server is None:
        left = _match_win(TennisState(**{**asdict(state), "server": "a"}), p_a_milli, p_b_milli)
        right = _match_win(TennisState(**{**asdict(state), "server": "b"}), p_a_milli, p_b_milli)
        return (left + right) / 2.0
    if state.tiebreak:
        p_set = _tiebreak_win(state, p_a_milli, p_b_milli, state.sets_a)
        next_a = _set_game_winner(state, "a")
        next_b = _set_game_winner(state, "b")
        return p_set * _match_win(next_a, p_a_milli, p_b_milli) + (1.0 - p_set) * _match_win(next_b, p_a_milli, p_b_milli)
    server_rate = pa if state.server == "a" else pb
    p_hold = _game_win(state.points_a, state.points_b, server_rate)
    p_game_a = p_hold if state.server == "a" else 1.0 - p_hold
    next_a = _set_game_winner(state, "a")
    next_b = _set_game_winner(state, "b")
    return p_game_a * _match_win(next_a, p_a_milli, p_b_milli) + (1.0 - p_game_a) * _match_win(next_b, p_a_milli, p_b_milli)


@lru_cache(maxsize=300_000)
def _tiebreak_win(state: TennisState, p_a_milli: int, p_b_milli: int, base_sets_a: int) -> float:
    """Exact tie-break win probability, solving the repeating two-point deuce tail."""
    if not state.tiebreak:
        return 1.0 if state.sets_a > base_sets_a else 0.0
    pa, pb = state.points_a, state.points_b
    if max(pa, pb) >= 7 and abs(pa - pb) >= 2:
        return 1.0 if pa > pb else 0.0
    if pa == pb and pa >= 6:
        # Four-point service cycle returns to deuce unless somebody wins by two.
        win_mass = 0.0
        return_mass = 0.0
        first = state.server or state.tiebreak_server or "a"
        server_cycle = (first, first, _next_server(first), _next_server(first))
        for mask in range(16):
            score_a, score_b, mass = pa, pb, 1.0
            terminal = None
            for index, server in enumerate(server_cycle):
                p_point_a = p_a_milli / 1000 if server == "a" else 1.0 - p_b_milli / 1000
                won = bool(mask & (1 << index))
                mass *= p_point_a if won else 1.0 - p_point_a
                if terminal is None:
                    score_a += won
                    score_b += not won
                    if max(score_a, score_b) >= 7 and abs(score_a - score_b) >= 2:
                        terminal = score_a > score_b
            if terminal is True:
                win_mass += mass
            elif terminal is None and score_a == score_b:
                return_mass += mass
        return win_mass / max(1e-12, 1.0 - return_mass)
    p_next = _point_prob(state, p_a_milli / 1000, p_b_milli / 1000)
    return p_next * _tiebreak_win(_advance_point(state, "a"), p_a_milli, p_b_milli, base_sets_a) + (1.0 - p_next) * _tiebreak_win(_advance_point(state, "b"), p_a_milli, p_b_milli, base_sets_a)


def _next_server(server: Optional[str]) -> Optional[str]:
    return {"a": "b", "b": "a"}.get(server)


def _normalise(values: Dict[str, float]) -> Dict[str, float]:
    total = sum(values.values())
    return {key: value / total for key, value in values.items()} if total else values


def _resolve_unknown_server(state: TennisState) -> List[Tuple[TennisState, float]]:
    """Branch the hidden server once and keep that assignment through the path."""
    if state.server in ("a", "b"):
        return [(state, 1.0)]
    if state.tiebreak and state.tiebreak_server in ("a", "b"):
        total = state.points_a + state.points_b
        switches = 0 if total == 0 else 1 + (total - 1) // 2
        serving = state.tiebreak_server if switches % 2 == 0 else _next_server(state.tiebreak_server)
        return [(TennisState(**{**asdict(state), "server": serving}), 1.0)]
    return [
        (TennisState(**{**asdict(state), "server": "a", "tiebreak_server": state.tiebreak_server or "a"}), 0.5),
        (TennisState(**{**asdict(state), "server": "b", "tiebreak_server": state.tiebreak_server or "b"}), 0.5),
    ]


def _point_scenarios(state: TennisState, p_a: float, p_b: float, depth: int = 3) -> List[dict]:
    branches: List[Tuple[TennisState, float, str]] = [
        (resolved, mass, "") for resolved, mass in _resolve_unknown_server(state)
    ]
    for _ in range(depth):
        next_branches = []
        for current, mass, path in branches:
            if current.sets_a >= current.sets_to_win or current.sets_b >= current.sets_to_win:
                next_branches.append((current, mass, path))
                continue
            p = _point_prob(current, p_a, p_b)
            next_branches.extend([
                (_advance_point(current, "a"), mass * p, path + "A"),
                (_advance_point(current, "b"), mass * (1 - p), path + "B"),
            ])
        branches = next_branches
    merged: Dict[TennisState, Tuple[float, List[str]]] = {}
    for final, probability, path in branches:
        mass, paths = merged.get(final, (0.0, []))
        merged[final] = (mass + probability, paths + [path])
    return [{"points": "|".join(paths), "probability": round(mass, 6), "state": asdict(final)} for final, (mass, paths) in sorted(merged.items(), key=lambda item: item[1][0], reverse=True)[:8]]


def _game_scenarios(state: TennisState, p_a: float, p_b: float, depth: int = 2) -> List[dict]:
    branches: List[Tuple[TennisState, float, str]] = [
        (resolved, mass, "") for resolved, mass in _resolve_unknown_server(state)
    ]
    for _ in range(depth):
        following = []
        for current, mass, path in branches:
            if current.sets_a >= current.sets_to_win or current.sets_b >= current.sets_to_win:
                following.append((current, mass, path))
                continue
            server = current.server
            if current.tiebreak:
                p_game_a = _tiebreak_win(
                    current, int(round(p_a * 1000)), int(round(p_b * 1000)), current.sets_a,
                )
            else:
                p_server = p_a if server == "a" else p_b
                p_hold = _game_win(current.points_a if not path else 0, current.points_b if not path else 0, round(p_server, 3))
                p_game_a = p_hold if server == "a" else 1.0 - p_hold
            following.extend([
                (_set_game_winner(current, "a"), mass * p_game_a, path + "A"),
                (_set_game_winner(current, "b"), mass * (1 - p_game_a), path + "B"),
            ])
        branches = following
    merged: Dict[TennisState, Tuple[float, List[str]]] = {}
    for final, probability, path in branches:
        mass, paths = merged.get(final, (0.0, []))
        merged[final] = (mass + probability, paths + [path])
    return [{"games": "|".join(paths), "probability": round(mass, 6), "state": asdict(final)} for final, (mass, paths) in sorted(merged.items(), key=lambda item: item[1][0], reverse=True)]


def _smoothed_rate(won: float, played: float, prior: float, strength: float = 80.0) -> Tuple[float, float]:
    """Shrink sparse serving/return samples toward the tour prior."""
    if played <= 0:
        return prior, 0.0
    return (won + prior * strength) / (played + strength), played / (played + strength)


def _live_serve_rates(states, base_a, base_b, best_of: int = 3):
    """Score polls are not point events: an apparently one-point delta may hide rallies.

    Keep the return shape for callers, but do not turn score-compatible transitions
    into training evidence unless a source supplies a unique point-event sequence.
    Fonbet score snapshots currently do not provide that guarantee.
    """
    return base_a, base_b, {"a": 0.0, "b": 0.0}


def player_serve_rates(session, player_a: str, player_b: str, cutoff, surface: str = "unknown") -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """Read real historical serve-point denominators available by the cutoff."""
    from datetime import timedelta
    from sqlalchemy import or_, select
    from db.models.external_tennis import ExtTennisServeLog
    from .players import canonical_player_name

    left, right = canonical_player_name(player_a), canonical_player_name(player_b)
    rates = {left: {"all": [0.0, 0.0], "surface": [0.0, 0.0]}, right: {"all": [0.0, 0.0], "surface": [0.0, 0.0]}}
    if not left or not right:
        return (0.62, 0.62, 0.0), (0.62, 0.62, 0.0)
    start = cutoff - timedelta(days=365)
    name_filters = []
    for name in (player_a, player_b):
        tokens = [token for token in canonical_player_name(name).split() if len(token) > 1]
        if tokens:
            name_filters.append(ExtTennisServeLog.player_name.contains(tokens[-1], autoescape=True))
    if not name_filters:
        return (0.62, 0.62, 0.0), (0.62, 0.62, 0.0)
    rows = session.execute(
        select(ExtTennisServeLog.player_name, ExtTennisServeLog.serve_points, ExtTennisServeLog.serve_points_won, ExtTennisServeLog.surface)
        .where(ExtTennisServeLog.available_at <= cutoff, ExtTennisServeLog.available_at >= start, or_(*name_filters))
        .order_by(ExtTennisServeLog.available_at)
    ).all()
    wanted_surface = (surface or "unknown").strip().lower()
    for name, played, won, row_surface in rows:
        row_surface = (row_surface or "unknown").strip().lower()
        key = canonical_player_name(name)
        if key not in rates or not played or float(played) <= 0:
            continue
        rates[key]["all"][0] += float(won or 0.0)
        rates[key]["all"][1] += float(played)
        if wanted_surface == "unknown" or row_surface in (wanted_surface, "unknown"):
            rates[key]["surface"][0] += float(won or 0.0)
            rates[key]["surface"][1] += float(played)
    def choose(key):
        sampled = rates[key]["surface"]
        if sampled[1] <= 0:
            sampled = rates[key]["all"]
        return _smoothed_rate(*sampled, prior=0.62)
    left_rate = choose(left)
    right_rate = choose(right)
    return (left_rate[0], 0.62, left_rate[1]), (right_rate[0], 0.62, right_rate[1])


def persist_event_forecasts(session, event, states, cutoff) -> int:
    """Store hybrid odds-free forecast snapshots for newly observed states."""
    import hashlib
    import json
    import uuid
    from db.models.predictions import TennisForecastSnapshot
    from contracts.predictions import TennisForecast

    best_of = int((event.metadata_json or {}).get("best_of") or 3)
    unavailable_reason = None
    state_value = None
    if states:
        latest = states[-1]
        observed_at = latest.observed_at
        if (event.status == "live" or event.is_live) and str(latest.status).lower() != "live":
            unavailable_reason = "NO_LIVE_STATE"
        else:
            state_value, error = parse_tennis_state(latest.score_detail, latest.sport_state, best_of=best_of)
            if error or state_value is None:
                unavailable_reason = error or "UNSUPPORTED_SCORE"
    else:
        observed_at = cutoff
        if event.status == "live" or event.is_live:
            unavailable_reason = "NO_LIVE_STATE"
        else:
            # Upcoming events still receive an odds-free prior forecast.
            state_value = TennisState(sets_to_win=(best_of if best_of in (3, 5) else 3) // 2 + 1)
    surface = str((event.metadata_json or {}).get("surface") or "unknown")
    # Player data must have been available by the actual calculation time. The
    # forecast may intentionally wait for initial profile preparation after the
    # latest score poll, so observed_at and calculated_at can differ.
    source_cutoff = cutoff
    serve_a, serve_b = player_serve_rates(session, event.participant_a_name, event.participant_b_name, source_cutoff, surface)
    from .player_metrics import load_player_metric, pair_features

    metric_a = load_player_metric(session, event.participant_a_id, source_cutoff)
    metric_b = load_player_metric(session, event.participant_b_id, source_cutoff)
    if metric_a and metric_a["metrics"].get("serve", {}).get("points", 0) > 0:
        serve_a = _smoothed_rate(
            metric_a["metrics"]["serve"].get("points_won", 0),
            metric_a["metrics"]["serve"].get("points", 0),
            prior=0.62,
        ) + (0.62,)
    if metric_b and metric_b["metrics"].get("serve", {}).get("points", 0) > 0:
        serve_b = _smoothed_rate(
            metric_b["metrics"]["serve"].get("points_won", 0),
            metric_b["metrics"]["serve"].get("points", 0),
            prior=0.62,
        ) + (0.62,)
    pair = pair_features(metric_a, metric_b)
    live_samples = {"a": 0.0, "b": 0.0}
    if state_value is not None:
        serve_a, serve_b, live_samples = _live_serve_rates(states, serve_a, serve_b, best_of)
    missing = ["NO_LIVE_STATE"] if not states else []
    if state_value is not None and not state_value.server:
        missing.append("SERVER_UNKNOWN")
    if serve_a[2] == 0:
        missing.append("SERVE_HISTORY_A_MISSING")
    if serve_b[2] == 0:
        missing.append("SERVE_HISTORY_B_MISSING")
    if metric_a is None:
        missing.append("PLAYER_METRICS_A_MISSING")
    if metric_b is None:
        missing.append("PLAYER_METRICS_B_MISSING")
    if states:
        missing.append("POINT_SEQUENCE_UNAVAILABLE")
    quality = 0.0 if unavailable_reason else 1.0 - min(1.0, len(missing) * 0.25)
    if state_value is None:
        value = {
            "schema_version": "1.0.0", "model_name": "tennis_score_baseline", "model_version": "tennis_v1.0.0",
            "state": {}, "probabilities": {}, "scenarios": {"next_3_points": [], "next_2_games": []},
            "data_quality": 0.0, "reasons": [unavailable_reason], "forecast_available": False,
            "unavailable_reason": unavailable_reason, "serve_point_probability": {}, "sample_weight": {},
        }
    else:
        value = forecast(state_value, serve_a=serve_a, serve_b=serve_b, data_quality=quality, reasons=missing)
        value["forecast_available"] = True
        value["data_quality_detail"] = {
            "historical_serve_sample_weight": value["sample_weight"],
            "live_point_sequence_confirmed": False,
            "live_score_state_count": len(states),
            "surface": surface,
        }
        value["model_uncertainty"] = {
            "serve_rate_a": round(1.0 - value["sample_weight"]["a"], 4),
            "serve_rate_b": round(1.0 - value["sample_weight"]["b"], 4),
        }
    value["player_metrics"] = {
        "a": {
            "participant_id": metric_a["participant_id"],
            "metrics_version": metric_a["metrics_version"],
            "available_at": metric_a["available_at"],
            "history_digest": metric_a["history_digest"],
            "sync_status": metric_a["sync_status"],
        } if metric_a else None,
        "b": {
            "participant_id": metric_b["participant_id"],
            "metrics_version": metric_b["metrics_version"],
            "available_at": metric_b["available_at"],
            "history_digest": metric_b["history_digest"],
            "sync_status": metric_b["sync_status"],
        } if metric_b else None,
        "pair_features": pair,
    }
    value["data_quality_detail"] = {
        **value.get("data_quality_detail", {}),
        "player_metrics_a_version": metric_a["metrics_version"] if metric_a else None,
        "player_metrics_b_version": metric_b["metrics_version"] if metric_b else None,
        "player_metrics_a_available_at": metric_a["available_at"] if metric_a else None,
        "player_metrics_b_available_at": metric_b["available_at"] if metric_b else None,
    }
    fingerprint_payload = {
        "state": value["state"], "serve": value["serve_point_probability"],
        "sample_weight": value["sample_weight"], "live_samples": live_samples,
        "player_metrics_a": metric_a["history_digest"] if metric_a else None,
        "player_metrics_b": metric_b["history_digest"] if metric_b else None,
        "unavailable_reason": unavailable_reason,
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True).encode()).hexdigest()
    # Lock the event row so concurrent polling cannot allocate the same
    # sequence number. Only collapse an identical adjacent state: A -> B -> A
    # must produce three snapshots.
    from db.models.events import Event
    session.query(Event.id).filter(Event.id == event.id).with_for_update().first()
    latest = session.query(TennisForecastSnapshot).filter_by(
        event_id=event.id, model_name=value["model_name"], model_version=value["model_version"],
    ).order_by(TennisForecastSnapshot.sequence_no.desc()).first()
    if latest and latest.state_digest == fingerprint:
        return 0
    sequence_no = (latest.sequence_no + 1) if latest else 1
    forecast_id = uuid.uuid4()
    packet = TennisForecast.from_forecast(
        str(event.id), value, observed_at, "tennis_odds_free_v2", str(forecast_id)
    )
    session.add(TennisForecastSnapshot(
        id=forecast_id,
        event_id=event.id, model_name=value["model_name"], model_version=value["model_version"],
        feature_version="tennis_odds_free_v2", sequence_no=sequence_no,
        observed_at=observed_at, state_digest=fingerprint,
        forecast_json=packet.model_dump(mode="json"),
    ))
    return 1


def forecast(
    state: TennisState,
    *,
    serve_a: Tuple[float, float, float] = (0.62, 0.62, 0.0),
    serve_b: Tuple[float, float, float] = (0.62, 0.62, 0.0),
    model_name: str = "tennis_score_baseline",
    version: str = "tennis_v1.0.0",
    data_quality: float = 0.0,
    reasons: Optional[Sequence[str]] = None,
) -> dict:
    """Forecast match/set and local paths. Inputs contain no odds or market probabilities."""
    p_a = min(0.9, max(0.35, float(serve_a[0])))
    p_b = min(0.9, max(0.35, float(serve_b[0])))
    weight_a, weight_b = float(serve_a[2]), float(serve_b[2])
    current_point = _point_prob(state, p_a, p_b)
    point_a = min(0.999, max(0.001, current_point))
    set_a = _match_win(state, int(round(p_a * 1000)), int(round(p_b * 1000)))
    # Evaluate the current-set winner by treating a set win as terminal.
    one_set = TennisState(0, 0, state.games_a, state.games_b, state.points_a, state.points_b, state.server, state.tiebreak, 1, state.tiebreak_server)
    p_set = _match_win(one_set, int(round(p_a * 1000)), int(round(p_b * 1000)))
    if state.tiebreak:
        p_game_a = point_a
    elif state.server is None:
        p_hold_a = _game_win(state.points_a, state.points_b, p_a)
        p_hold_b = _game_win(state.points_a, state.points_b, p_b)
        p_game_a = (p_hold_a + 1.0 - p_hold_b) / 2.0
    else:
        p_game_hold = _game_win(state.points_a, state.points_b, p_a if state.server == "a" else p_b)
        p_game_a = p_game_hold if state.server == "a" else 1.0 - p_game_hold
    return {
        "schema_version": "1.0.0", "model_name": model_name, "model_version": version,
        "state": asdict(state),
        "probabilities": {
            "next_point_a": round(point_a, 6), "next_game_a": round(p_game_a, 6),
            "current_set_a": round(p_set, 6), "match_a": round(set_a, 6),
        },
        "serve_point_probability": {"a": round(p_a, 6), "b": round(p_b, 6)},
        "data_quality": round(max(0.0, min(1.0, data_quality)), 4),
        "reasons": list(reasons or []),
        "scenarios": {"next_3_points": _point_scenarios(state, p_a, p_b), "next_2_games": _game_scenarios(state, p_a, p_b)},
        "sample_weight": {"a": round(weight_a, 4), "b": round(weight_b, 4)},
    }

