"""As-of rolling team/player match stats from finished_events — shared by LGB + GRU."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from neurobet_filters import sport_top

from .vocab import team_index

DEFAULT_STATS_WINDOW = int(os.getenv("NEURALBET_TEAM_STATS_WINDOW", "40"))
STATS_UNKNOWN = 0.5

# Fixed KB / LGB vector length — keep in sync with model.KB_CONTEXT_DIM.
TEAM_STATS_DIM = 12
TEAM_STATS_FEATURE_NAMES = [
    "t1_avg_scored",
    "t1_avg_conceded",
    "t1_win_rate",
    "t1_loss_rate",
    "t1_period_win_rate",
    "t2_avg_scored",
    "t2_avg_conceded",
    "t2_win_rate",
    "t2_loss_rate",
    "t2_period_win_rate",
    "h2h_scored_diff",
    "form_vs_opp",
]

# Typical points scored per side (≈ half of a normal total) for 0–1 normalisation.
_TYPICAL_SCORED = {
    "футбол": 1.5,
    "баскетбол": 90.0,
    "волейбол": 22.0,
    "теннис": 12.0,
    "настольный теннис": 55.0,
    "хоккей": 2.5,
    "гандбол": 28.0,
}
_DEFAULT_TYPICAL = 10.0

TeamKey = Tuple[int, str]
H2HKey = Tuple[int, int, str]
# (scored, conceded, won, lost, period_wins, period_decided)
MatchObs = Tuple[float, float, int, int, int, int]
# H2H from the lower team_idx side: (scored_lo, conceded_lo, win_lo)
H2HObs = Tuple[float, float, int]

EventStatsKey = Any  # event_id


def _row_get(row: Any, key: str, default=None):
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _row_finished_at(row: Any) -> str:
    return str(_row_get(row, "finished_at") or "")


def _team_key(team: str, sport_path: str) -> TeamKey:
    return (team_index(team), sport_top(sport_path))


def _h2h_key(team_a: str, team_b: str, sport_path: str) -> H2HKey:
    ia, ib = team_index(team_a), team_index(team_b)
    lo, hi = (ia, ib) if ia <= ib else (ib, ia)
    return (lo, hi, sport_top(sport_path))


def _parse_periods(raw: Any) -> List[Tuple[int, int]]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        parsed = raw
    else:
        try:
            parsed = json.loads(raw)
        except Exception:
            return []
    out: List[Tuple[int, int]] = []
    if not isinstance(parsed, list):
        return out
    for p in parsed:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            try:
                out.append((int(p[0]), int(p[1])))
            except (TypeError, ValueError):
                continue
    return out


def _norm_scored(raw: float, sport_path: str) -> float:
    typical = _TYPICAL_SCORED.get(sport_top(sport_path), _DEFAULT_TYPICAL)
    if typical <= 0:
        return STATS_UNKNOWN
    return max(0.0, min(1.0, float(raw) / typical))


def _norm_diff(raw: float, sport_path: str) -> float:
    """Map signed scored-diff into [0, 1] with 0.5 = even."""
    typical = _TYPICAL_SCORED.get(sport_top(sport_path), _DEFAULT_TYPICAL)
    if typical <= 0:
        return STATS_UNKNOWN
    return max(0.0, min(1.0, 0.5 + 0.5 * (float(raw) / typical)))


def _period_tally(periods: Sequence[Tuple[int, int]], side: int) -> Tuple[int, int]:
    wins = 0
    decided = 0
    for a, b in periods:
        if a == b:
            continue
        decided += 1
        if side == 0 and a > b:
            wins += 1
        elif side == 1 and b > a:
            wins += 1
    return wins, decided


def _rate(wins: int, n: int) -> float:
    if n <= 0:
        return STATS_UNKNOWN
    return float(wins) / float(n)


def _stats_from_obs(obs: Sequence[MatchObs], sport_path: str, window: int) -> List[float]:
    """Return [avg_scored, avg_conceded, wr, lr, period_wr] for one side."""
    tail = list(obs)[-window:]
    if not tail:
        return [STATS_UNKNOWN] * 5
    n = len(tail)
    scored = sum(o[0] for o in tail) / n
    conceded = sum(o[1] for o in tail) / n
    wins = sum(o[2] for o in tail)
    losses = sum(o[3] for o in tail)
    p_wins = sum(o[4] for o in tail)
    p_n = sum(o[5] for o in tail)
    return [
        _norm_scored(scored, sport_path),
        _norm_scored(conceded, sport_path),
        _rate(wins, n),
        _rate(losses, n),
        _rate(p_wins, p_n) if p_n > 0 else STATS_UNKNOWN,
    ]


def _h2h_from_obs(
    obs: Sequence[H2HObs],
    *,
    flip: bool,
    sport_path: str,
    window: int,
) -> Tuple[float, float]:
    """(h2h_scored_diff_norm, h2h_win_rate) from the queried team's perspective."""
    tail = list(obs)[-window:]
    if not tail:
        return STATS_UNKNOWN, STATS_UNKNOWN
    diffs: List[float] = []
    wins = 0
    n = len(tail)
    for scored_lo, conceded_lo, win_lo in tail:
        if flip:
            diffs.append(float(conceded_lo - scored_lo))
            if scored_lo != conceded_lo and not win_lo:
                wins += 1
        else:
            diffs.append(float(scored_lo - conceded_lo))
            wins += int(win_lo)
    avg_diff = sum(diffs) / n
    return _norm_diff(avg_diff, sport_path), _rate(wins, n)


def unknown_stats_vector() -> List[float]:
    return [STATS_UNKNOWN] * TEAM_STATS_DIM


def _combine_vector(
    t1: Sequence[float],
    t2: Sequence[float],
    h2h_diff: float,
    form_vs_opp: float,
) -> List[float]:
    return [
        float(t1[0]), float(t1[1]), float(t1[2]), float(t1[3]), float(t1[4]),
        float(t2[0]), float(t2[1]), float(t2[2]), float(t2[3]), float(t2[4]),
        float(h2h_diff),
        float(form_vs_opp),
    ]


def _vector_from_buckets(
    team_buckets: Dict[TeamKey, List[MatchObs]],
    h2h_buckets: Dict[H2HKey, List[H2HObs]],
    team_1: str,
    team_2: str,
    sport_path: str,
    window: int,
) -> List[float]:
    t1 = (team_1 or "").strip()
    t2 = (team_2 or "").strip()
    if not t1 and not t2:
        return unknown_stats_vector()

    t1_stats = (
        _stats_from_obs(team_buckets.get(_team_key(t1, sport_path), []), sport_path, window)
        if t1 else [STATS_UNKNOWN] * 5
    )
    t2_stats = (
        _stats_from_obs(team_buckets.get(_team_key(t2, sport_path), []), sport_path, window)
        if t2 else [STATS_UNKNOWN] * 5
    )

    h2h_diff = STATS_UNKNOWN
    h2h_wr = STATS_UNKNOWN
    if t1 and t2:
        key = _h2h_key(t1, t2, sport_path)
        i1, i2 = team_index(t1), team_index(t2)
        flip = i1 > i2
        h2h_diff, h2h_wr = _h2h_from_obs(
            h2h_buckets.get(key, []),
            flip=flip,
            sport_path=sport_path,
            window=window,
        )

    # Relative form: mid at equality; prefer H2H win-rate when available.
    if h2h_wr != STATS_UNKNOWN:
        form_vs_opp = h2h_wr
    elif t1_stats[2] != STATS_UNKNOWN and t2_stats[2] != STATS_UNKNOWN:
        form_vs_opp = max(0.0, min(1.0, 0.5 + 0.5 * (t1_stats[2] - t2_stats[2])))
    else:
        form_vs_opp = STATS_UNKNOWN

    return _combine_vector(t1_stats, t2_stats, h2h_diff, form_vs_opp)


def _append_event(
    team_buckets: Dict[TeamKey, List[MatchObs]],
    h2h_buckets: Dict[H2HKey, List[H2HObs]],
    row: Any,
) -> None:
    t1 = (_row_get(row, "team_1") or "").strip()
    t2 = (_row_get(row, "team_2") or "").strip()
    if not t1 and not t2:
        return
    sport_path = _row_get(row, "sport_path") or ""
    try:
        s1 = float(_row_get(row, "score_1") or 0)
        s2 = float(_row_get(row, "score_2") or 0)
    except (TypeError, ValueError):
        return
    periods = _parse_periods(_row_get(row, "period_scores_json"))
    p1_w, p1_n = _period_tally(periods, 0)
    p2_w, p2_n = _period_tally(periods, 1)
    w1 = 1 if s1 > s2 else 0
    l1 = 1 if s1 < s2 else 0
    w2 = 1 if s2 > s1 else 0
    l2 = 1 if s2 < s1 else 0

    if t1:
        team_buckets.setdefault(_team_key(t1, sport_path), []).append(
            (s1, s2, w1, l1, p1_w, p1_n)
        )
    if t2:
        team_buckets.setdefault(_team_key(t2, sport_path), []).append(
            (s2, s1, w2, l2, p2_w, p2_n)
        )
    if t1 and t2:
        i1, i2 = team_index(t1), team_index(t2)
        if i1 <= i2:
            h2h_buckets.setdefault(_h2h_key(t1, t2, sport_path), []).append(
                (s1, s2, w1)
            )
        else:
            h2h_buckets.setdefault(_h2h_key(t1, t2, sport_path), []).append(
                (s2, s1, w2)
            )


def build_team_stats_index(
    rows: list[Any],
    window: int = DEFAULT_STATS_WINDOW,
) -> Dict[str, Any]:
    """
    Live cache: rolling team + H2H buckets after all rows.

    Shape: {"team": {TeamKey: [MatchObs,...]}, "h2h": {H2HKey: [H2HObs,...]}, "window": int}
    Kept as raw tails so lookup can recompute windows consistently.
    """
    team_buckets: Dict[TeamKey, List[MatchObs]] = {}
    h2h_buckets: Dict[H2HKey, List[H2HObs]] = {}
    for row in sorted(rows, key=_row_finished_at):
        _append_event(team_buckets, h2h_buckets, row)

    # Trim to window tails to keep the live cache compact.
    team_trim = {k: v[-window:] for k, v in team_buckets.items() if v}
    h2h_trim = {k: v[-window:] for k, v in h2h_buckets.items() if v}
    return {"team": team_trim, "h2h": h2h_trim, "window": int(window)}


def build_team_stats_asof_lookup(
    rows: list[Any],
    window: int = DEFAULT_STATS_WINDOW,
) -> Dict[EventStatsKey, List[float]]:
    """
    Per-event KB vector at decision time: snapshot *before* applying that event's scores.

    Rows: finished_events fields (event_id, team_1/2, sport_path, score_1/2,
    period_scores_json, finished_at). Duplicate event_ids keep the first (earliest) snapshot.
    """
    team_buckets: Dict[TeamKey, List[MatchObs]] = {}
    h2h_buckets: Dict[H2HKey, List[H2HObs]] = {}
    lookup: Dict[EventStatsKey, List[float]] = {}

    for row in sorted(rows, key=_row_finished_at):
        eid = _row_get(row, "event_id")
        t1 = _row_get(row, "team_1") or ""
        t2 = _row_get(row, "team_2") or ""
        sport_path = _row_get(row, "sport_path") or ""
        if eid is not None and eid not in lookup:
            lookup[eid] = _vector_from_buckets(
                team_buckets, h2h_buckets, t1, t2, sport_path, window,
            )
        _append_event(team_buckets, h2h_buckets, row)

    return lookup


def lookup_team_stats_vector(
    cache: Optional[Dict[str, Any]],
    team_1: str,
    team_2: str,
    sport_path: str,
) -> List[float]:
    if not cache:
        return unknown_stats_vector()
    team_buckets = cache.get("team") or {}
    h2h_buckets = cache.get("h2h") or {}
    window = int(cache.get("window") or DEFAULT_STATS_WINDOW)
    return _vector_from_buckets(
        team_buckets, h2h_buckets, team_1, team_2, sport_path, window,
    )
