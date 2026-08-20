"""Rolling player/team form from resolved archive — refreshed with LightGBM refit."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from neurobet_filters import TOTAL_FACTOR_IDS, sport_top

from .vocab import market_family_index, team_index

DEFAULT_FORM_WINDOW = int(os.getenv("NEURALBET_TEAM_FORM_WINDOW", "40"))
FORM_UNKNOWN = 0.5

FACTORS_W1 = frozenset({921})
FACTORS_W2 = frozenset({923})
FACTORS_DRAW = frozenset({922})

FormKey = Tuple[int, str, int]
BetKey = Tuple[Any, int, str, str]


def _form_key(team: str, sport_path: str, factor_id: int) -> FormKey:
    return (team_index(team), sport_top(sport_path), market_family_index(factor_id))


def _row_get(row: Any, key: str, default=None):
    if hasattr(row, "get"):
        return row.get(key, default)
    return row[key] if key in row else default


def _row_finished_at(row: Any) -> str:
    return str(_row_get(row, "finished_at") or "")


def teams_for_form_update(team_1: str, team_2: str, factor_id: int) -> List[str]:
    """
    Which team(s) receive this bet's is_win when updating form buckets.

    Moneyline P1/P2 credit only the backed side; draw (922) updates neither;
    totals credit both sides with the same market outcome.
    """
    fid = int(factor_id or 0)
    t1 = (team_1 or "").strip()
    t2 = (team_2 or "").strip()
    if fid in FACTORS_W1:
        return [t1] if t1 else []
    if fid in FACTORS_W2:
        return [t2] if t2 else []
    if fid in FACTORS_DRAW:
        return []
    out: List[str] = []
    if t1:
        out.append(t1)
    if t2:
        out.append(t2)
    return out


def _form_from_buckets(
    buckets: Dict[FormKey, List[int]],
    team: str,
    sport_path: str,
    factor_id: int,
    window: int,
) -> float:
    if not team:
        return FORM_UNKNOWN
    wins = buckets.get(_form_key(team, sport_path, factor_id), [])
    tail = wins[-window:]
    if not tail:
        return FORM_UNKNOWN
    return sum(tail) / len(tail)


def build_team_form_index(rows: list[Any], window: int = DEFAULT_FORM_WINDOW) -> Dict[FormKey, float]:
    """Win-rate per (team_idx, sport, market_family) over last `window` resolved rows."""
    buckets: Dict[FormKey, List[int]] = {}
    for row in sorted(rows, key=_row_finished_at):
        is_win = _row_get(row, "is_win")
        if is_win is None:
            continue
        sport_path = _row_get(row, "sport_path") or ""
        factor_id = int(_row_get(row, "factor_id") or 0)
        for team in teams_for_form_update(
            _row_get(row, "team_1") or "",
            _row_get(row, "team_2") or "",
            factor_id,
        ):
            key = _form_key(team, sport_path, factor_id)
            buckets.setdefault(key, []).append(int(is_win))

    out: Dict[FormKey, float] = {}
    for key, wins in buckets.items():
        tail = wins[-window:]
        if tail:
            out[key] = sum(tail) / len(tail)
    return out


def build_team_form_asof_lookup(
    rows: list[Any],
    window: int = DEFAULT_FORM_WINDOW,
) -> Dict[BetKey, Tuple[float, float]]:
    """
    Per-bet form at decision time: snapshot *before* applying that row's outcome.

    Rows should include event_id, factor_id, parameter, market_prefix, team_1/2,
    sport_path, is_win, finished_at.
    """
    buckets: Dict[FormKey, List[int]] = {}
    lookup: Dict[BetKey, Tuple[float, float]] = {}

    for row in sorted(rows, key=_row_finished_at):
        sport_path = _row_get(row, "sport_path") or ""
        factor_id = int(_row_get(row, "factor_id") or 0)
        t1 = _row_get(row, "team_1") or ""
        t2 = _row_get(row, "team_2") or ""
        bet_key: BetKey = (
            _row_get(row, "event_id"),
            factor_id,
            _row_get(row, "parameter") or "",
            _row_get(row, "market_prefix") or "",
        )
        lookup[bet_key] = (
            _form_from_buckets(buckets, t1, sport_path, factor_id, window),
            _form_from_buckets(buckets, t2, sport_path, factor_id, window),
        )

        is_win = _row_get(row, "is_win")
        if is_win is None:
            continue
        for team in teams_for_form_update(t1, t2, factor_id):
            key = _form_key(team, sport_path, factor_id)
            buckets.setdefault(key, []).append(int(is_win))

    return lookup


def lookup_team_form(
    cache: Optional[Dict[FormKey, float]],
    team: str,
    sport_path: str,
    factor_id: int,
) -> float:
    if not cache or not team:
        return FORM_UNKNOWN
    return float(cache.get(_form_key(team, sport_path, factor_id), FORM_UNKNOWN))


def is_total_market(factor_id: int) -> bool:
    return int(factor_id) in TOTAL_FACTOR_IDS
