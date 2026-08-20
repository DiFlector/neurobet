"""Bookmaker overround (sum of 1/coeff across sibling outcomes).

One grouping table for live inference, archive, and training — previously
backend only summed football 930/931 totals while ai_service summed every
TOTAL_* id, so table-tennis live overround and archived overround_close
were different features.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from neurobet_filters import TOTAL_OVER_IDS, TOTAL_UNDER_IDS

_OVERROUND_MATCH_RESULT = {921, 922, 923}
_OVERROUND_DOUBLE_CHANCE = {924, 1571, 925, 926}
_OVERROUND_TOTAL = TOTAL_OVER_IDS | TOTAL_UNDER_IDS

OVERROUND_EXPECTED_SIZE = {"match_result": 3, "double_chance": 3, "total": 2}
# Sentinel for LightGBM: real overrounds are always > 1.0.
OVERROUND_UNKNOWN = 0.0
# A complete sibling set at a book is ~1.03–1.15. Values above this almost
# always mean mixed periods (match 1X2 + 1st-half 1X2) were summed together.
OVERROUND_SANE_MAX = 1.25

# (group, parameter, prefix). Prefix keeps period 1X2 / DC from mixing with
# the main-match siblings; totals also key on the line so "4-й сет ТМ 19"
# does not mix with match-total 19.
OverroundKey = Tuple[str, str, str]


def overround_group_key(
    factor_id: Optional[int],
    parameter: str = "",
    market_prefix: str = "",
) -> Optional[OverroundKey]:
    if factor_id is None:
        return None
    fid = int(factor_id)
    param = str(parameter or "")
    prefix = str(market_prefix or "")
    if fid in _OVERROUND_MATCH_RESULT:
        return ("match_result", "", prefix)
    if fid in _OVERROUND_DOUBLE_CHANCE:
        return ("double_chance", "", prefix)
    if fid in _OVERROUND_TOTAL:
        return ("total", param, prefix)
    return None


def overround_from_grouped(grouped: Dict[OverroundKey, List[float]]) -> Dict[OverroundKey, float]:
    out: Dict[OverroundKey, float] = {}
    for key, coeffs in grouped.items():
        need = OVERROUND_EXPECTED_SIZE.get(key[0], 0)
        if need and len(coeffs) >= need:
            out[key] = sum(1.0 / c for c in coeffs)
    return out


def accumulate_overround(
    rows: Iterable[Any],
    *,
    event_id_key: str = "event_id",
) -> Dict[Tuple[Any, OverroundKey], float]:
    """Latest-snapshot overround per (event_id, group key).

    `rows` need factor_id, parameter, coefficient, and optionally
    market_prefix / event_id. Partial sibling sets are dropped.
    """
    grouped: Dict[Tuple[Any, OverroundKey], List[float]] = {}
    for r in rows:
        get = r.get if hasattr(r, "get") else lambda k, d=None: r[k] if k in r else d
        fid = get("factor_id")
        param = str(get("parameter") or "")
        prefix = str(get("market_prefix") or "")
        key = overround_group_key(fid, param, prefix)
        if key is None:
            continue
        coeff = get("coefficient")
        if not coeff or float(coeff) <= 1.0:
            continue
        eid = get(event_id_key)
        grouped.setdefault((eid, key), []).append(float(coeff))
    return {
        (eid, key): value
        for (eid, key), coeffs in grouped.items()
        for value in [overround_from_grouped({key: coeffs}).get(key)]
        if value is not None
    }


def overround_at_latest(
    latest_by_market: Dict[Tuple[int, str, str], float],
    factor_id: int,
    parameter: str = "",
    market_prefix: str = "",
) -> Optional[float]:
    """Overround from a {(factor_id, parameter, prefix): coeff} map."""
    want = overround_group_key(factor_id, parameter, market_prefix)
    if want is None:
        return None
    coeffs: List[float] = []
    for (fid, param, prefix), coeff in latest_by_market.items():
        if overround_group_key(fid, param, prefix) == want and coeff and coeff > 1.0:
            coeffs.append(float(coeff))
    got = overround_from_grouped({want: coeffs})
    return got.get(want)
