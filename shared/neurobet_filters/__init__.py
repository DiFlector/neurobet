"""
Single source of truth for NeuroBet sample/betting filters.

Two layers, on purpose:

- Universe (sport / factor_id / total line) — training, live inference, backtest
  fetch, UI, and stats. Win-head still sees every row inside this universe.
- Live gates (coefficient band, min EV, min market support) — staking real money,
  the "win" LIVE list, backtest "would bet", training bankroll/tuner. Thin markets
  and 1.0–1.5 shorts stay in the gradient; they just never get a stake.

Add a sport or total-line window here and SQL + Python pick it up everywhere.
Add a "don't stake if …" condition in `passes_live_gates` / `bet_band_sql`.
"""
import os
import re
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Universe — top-level sport_path segment, exact match (so "теннис" does not
# swallow "настольный теннис" / "падел-теннис", and "баскетбол" does not swallow
# "баскетбол 3x3").
# ---------------------------------------------------------------------------
ALLOWED_SPORTS = frozenset({
    "футбол",
    "баскетбол",
    "настольный теннис",
    "волейбол",
    "теннис",
})

# Totals including sport-specific main-line IDs (1848/1849 = table-tennis
# "Основные ставки | Тотал"). Mirrors backend/database.py FACTORS_TOTAL_OVER/UNDER.
TOTAL_OVER_IDS = frozenset({
    930, 940, 1696, 1727, 1730, 1733, 1736, 1739, 1793, 1796, 1799, 1802, 1805, 1848,
})
TOTAL_UNDER_IDS = frozenset({
    931, 941, 1697, 1728, 1731, 1734, 1737, 1791, 1794, 1797, 1800, 1803, 1806, 1849,
})
TOTAL_FACTOR_IDS = TOTAL_OVER_IDS | TOTAL_UNDER_IDS

# П1 / П2 + football draw + match totals only. Handicaps, individual totals, double
# chance and the long tail stay out of the gradient and out of the live allocator.
# Draw (922) is football-only — in basketball/tennis it is almost a constant "won't
# happen" and teaches nothing.
DRAW_FACTOR_ID = 922
ALLOWED_FACTOR_IDS = frozenset({921, DRAW_FACTOR_ID, 923} | TOTAL_FACTOR_IDS)

# Inclusive [lo, hi] on the total *line* (finished_bets.parameter / latest_odds.parameter).
# Period/set-point junk and match-long point sums fall outside these and are dropped.
TOTAL_LINE_RANGES: dict[str, Tuple[float, float]] = {
    "футбол": (1.5, 4.5),
    "баскетбол": (100.0, 280.0),
    "настольный теннис": (15.0, 25.0),
    "волейбол": (35.0, 55.0),
    "теннис": (6.0, 15.0),
}

# ---------------------------------------------------------------------------
# Live gates — same env vars both containers already read from the shared .env.
# ---------------------------------------------------------------------------
MIN_BET_COEFF = float(os.getenv("NEURALBET_MIN_BET_COEFF", "1.5"))
MAX_BET_COEFF = float(os.getenv("NEURALBET_MAX_BET_COEFF", "2.0"))
MIN_BET_EDGE_PCT = float(os.getenv("NEURALBET_MIN_BET_EDGE_PCT", "3.0"))
MIN_MARKET_SUPPORT = int(os.getenv("NEURALBET_MIN_MARKET_SUPPORT", "150"))


def sport_top(sport_path: Optional[str]) -> str:
    return (sport_path or "").split("/")[0].strip().lower()


def parse_total_line(parameter: Optional[str]) -> Optional[float]:
    if parameter is None:
        return None
    raw = str(parameter).strip().replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def in_universe(
    sport_path: Optional[str],
    factor_id: Optional[int],
    parameter: Optional[str] = None,
) -> bool:
    sport = sport_top(sport_path)
    if is_fast_format_sport_path(sport_path):
        return False
    if sport not in ALLOWED_SPORTS or factor_id is None:
        return False
    fid = int(factor_id)
    if fid not in ALLOWED_FACTOR_IDS:
        return False
    if fid == DRAW_FACTOR_ID and sport != "футбол":
        return False
    if fid in TOTAL_FACTOR_IDS:
        line = parse_total_line(parameter)
        bounds = TOTAL_LINE_RANGES.get(sport)
        if line is None or bounds is None:
            return False
        return bounds[0] <= line <= bounds[1]
    return True


# Simulated/compressed formats that finish faster than we can snapshot a real score
# (Setka Cup, NBA 2K Esportsbattle 4Х5, "2x4 мин", …). Under 126.5 settled as a win
# on a frozen 4:4 while Fonbet's coupon was 69:59 — same class as the earlier
# Esportsbattle under that graded 64:53 against a 76:57 final. Excluded from the
# universe (inference, training, stats) AND skipped at parse time.
_FAST_FORMAT_SPORT_PATH_SUBSTRINGS = (
    "setka cup", "world tennis", "tt cup", "online live league",
    "nba 2k", "2k26", "esportsbattle",
)
_FAST_ESPORTS_FORMAT_RE = re.compile(r"\d+\s*[xх]\s*\d+\s*мин", re.IGNORECASE)
# SQL for universe_sql — same tell, case-insensitive. Keep in sync with the Python
# matcher above. Do not drop the "мин" on the NxM branch: real amateur hockey
# "RHL 3x10" is a ~30-minute game we still want.
FAST_FORMAT_SPORT_SQL = (
    r"(nba 2k|2k26|esportsbattle|setka cup|world tennis|tt cup|online live league|"
    r"[0-9]+\s*[xх]\s*[0-9]+\s*мин)"
)


def is_fast_format_sport_path(sport_path: Optional[str]) -> bool:
    sp = (sport_path or "").lower()
    if any(s in sp for s in _FAST_FORMAT_SPORT_PATH_SUBSTRINGS):
        return True
    return bool(_FAST_ESPORTS_FORMAT_RE.search(sp))


def in_bet_band(coeff: float) -> bool:
    return MIN_BET_COEFF <= coeff <= MAX_BET_COEFF


def in_verdict_train_band(coeff: float) -> bool:
    """Decision-head training drops longs at/above MAX_BET_COEFF (a free 'will lose'
    label that drowns the residual). Inclusive bet-band still stakes exactly MAX."""
    return coeff < MAX_BET_COEFF


def live_gate_skip_reason(
    coeff: float,
    expected_roi: float,
    support_count: Optional[int] = None,
) -> Optional[str]:
    """Why this candidate is not staked, or None if it clears every live gate.
    `support_count is None` means 'don't check' (empty cache fails open)."""
    if not in_bet_band(coeff):
        return "coeff"
    if expected_roi < MIN_BET_EDGE_PCT:
        return "edge"
    if support_count is not None and support_count < MIN_MARKET_SUPPORT:
        return "support"
    return None


def passes_live_gates(
    coeff: float,
    expected_roi: float,
    support_count: Optional[int] = None,
) -> bool:
    return live_gate_skip_reason(coeff, expected_roi, support_count) is None


def universe_sql_params() -> Tuple[list, list]:
    """Bind values for the two `= ANY(%s)` placeholders in `universe_sql`."""
    return list(ALLOWED_SPORTS), list(ALLOWED_FACTOR_IDS)


def universe_line_sql(event_alias: str, bet_alias: str) -> str:
    """Draw-only-in-football + per-sport total-line window. No extra bind params —
    the factor-id list is inlined so every caller of universe_sql stays a 2-placeholder
    sports/factors pair. Non-numeric parameter values fail the regex and drop."""
    sport = f"LOWER(TRIM(SPLIT_PART({event_alias}.sport_path, '/', 1)))"
    raw = f"BTRIM(CAST({bet_alias}.parameter AS TEXT))"
    line = (
        f"(CASE WHEN {raw} ~ '^[0-9]+(\\.[0-9]+)?$' "
        f"THEN CAST({raw} AS DOUBLE PRECISION) ELSE NULL END)"
    )
    total_ids = ",".join(str(i) for i in sorted(TOTAL_FACTOR_IDS))
    cases = " ".join(
        f"WHEN '{name}' THEN {line} BETWEEN {lo} AND {hi}"
        for name, (lo, hi) in TOTAL_LINE_RANGES.items()
    )
    return f"""
      AND ({bet_alias}.factor_id <> {DRAW_FACTOR_ID} OR {sport} = 'футбол')
      AND (
        {bet_alias}.factor_id NOT IN ({total_ids})
        OR (CASE {sport} {cases} ELSE FALSE END)
      )
    """


def universe_sql(event_alias: str, bet_alias: str) -> str:
    """Sport + factor_id ANY() placeholders, then draw/line predicates. Bind
    `*universe_sql_params()` (or the two lists) immediately after any earlier params."""
    return (
        f" AND LOWER(TRIM(SPLIT_PART({event_alias}.sport_path, '/', 1))) = ANY(%s)"
        f" AND {bet_alias}.factor_id = ANY(%s)"
        f" AND {event_alias}.sport_path !~* '{FAST_FORMAT_SPORT_SQL}'"
        + universe_line_sql(event_alias, bet_alias)
    )


def bet_band_sql(coeff_expr: str, roi_expr: Optional[str] = None) -> Tuple[str, list]:
    """SQL fragment + bind params for the coefficient band and optional min-EV gate."""
    sql = f" AND {coeff_expr} >= %s AND {coeff_expr} <= %s"
    params: list = [MIN_BET_COEFF, MAX_BET_COEFF]
    if roi_expr:
        sql += f" AND {roi_expr} >= %s"
        params.append(MIN_BET_EDGE_PCT)
    return sql, params
