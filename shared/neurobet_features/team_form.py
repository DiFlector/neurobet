"""Rolling player/team form from resolved archive — refreshed with LightGBM refit."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Tuple

from neurobet_filters import TOTAL_FACTOR_IDS, sport_top

from .vocab import market_family_index, team_index

DEFAULT_FORM_WINDOW = int(os.getenv("NEURALBET_TEAM_FORM_WINDOW", "40"))
FORM_UNKNOWN = 0.5


def _form_key(team: str, sport_path: str, factor_id: int) -> Tuple[str, str, int]:
    return (team_index(team), sport_top(sport_path), market_family_index(factor_id))


def build_team_form_index(rows: list[Any], window: int = DEFAULT_FORM_WINDOW) -> Dict[Tuple[int, str, int], float]:
    """Win-rate / total-hit-rate per (team_idx, sport, market_family) over last `window` rows."""
    buckets: Dict[Tuple[int, str, int], list[int]] = {}
    for row in rows:
        get = row.get if hasattr(row, "get") else lambda k, d=None: row[k] if k in row else d
        is_win = get("is_win")
        if is_win is None:
            continue
        sport_path = get("sport_path") or ""
        factor_id = int(get("factor_id") or 0)
        for team in (get("team_1") or "", get("team_2") or ""):
            if not team:
                continue
            key = _form_key(team, sport_path, factor_id)
            buckets.setdefault(key, []).append(int(is_win))

    out: Dict[Tuple[int, str, int], float] = {}
    for key, wins in buckets.items():
        tail = wins[-window:]
        if not tail:
            continue
        out[key] = sum(tail) / len(tail)
    return out


def lookup_team_form(
    cache: Optional[Dict[Tuple[int, str, int], float]],
    team: str,
    sport_path: str,
    factor_id: int,
) -> float:
    if not cache or not team:
        return FORM_UNKNOWN
    return float(cache.get(_form_key(team, sport_path, factor_id), FORM_UNKNOWN))


def is_total_market(factor_id: int) -> bool:
    return int(factor_id) in TOTAL_FACTOR_IDS
