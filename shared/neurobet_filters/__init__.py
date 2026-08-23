"""
Single source of truth for NeuroBet sample/betting filters.

Two layers, on purpose:

- Universe (sport / factor_id / total line) — training, live inference, backtest
  fetch, UI, and stats. Win-head still sees every row inside this universe.
- Live gates (coefficient band, min EV, min market support, live sports / markets) —
  staking real money, the "win" LIVE list, backtest "would bet", training
  bankroll/tuner. Thin markets and 1.0–1.5 shorts stay in the gradient; they just
  never get a stake.

Add a sport or total-line window here and SQL + Python pick it up everywhere.
Add a "don't stake if …" condition in `passes_live_gates` / `bet_band_sql`.
Env: `NEURALBET_LIVE_STAKE_SPORTS`, `NEURALBET_LIVE_STAKE_MARKETS`,
`NEURALBET_MIN_BET_EDGE_PCT`, `NEURALBET_BRIER_SPORT_GATE`.
"""
import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

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
MIN_BET_EDGE_PCT = float(os.getenv("NEURALBET_MIN_BET_EDGE_PCT", "5.0"))
MIN_MARKET_SUPPORT = int(os.getenv("NEURALBET_MIN_MARKET_SUPPORT", "150"))

# Live staking ceiling — training/inference/backtest universe stays ALLOWED_SPORTS.
# Actual live sports are this set ∩ last-backtest Brier winners (see
# `in_live_stake_sport`). Default ceiling = full universe so a sport can auto-join
# when its Brier starts beating the market.
_LIVE_STAKE_SPORTS_RAW = os.getenv(
    "NEURALBET_LIVE_STAKE_SPORTS",
    "настольный теннис,теннис,баскетбол,футбол,волейбол",
).strip().lower()
LIVE_STAKE_SPORTS: frozenset[str] | None = (
    None
    if _LIVE_STAKE_SPORTS_RAW in ("", "*", "all")
    else frozenset(
        s.strip().lower() for s in _LIVE_STAKE_SPORTS_RAW.split(",") if s.strip()
    )
)

# Live staking markets — training/inference still see П1/П2/draw + totals.
# Default: totals + moneyline (no draw stake until OOS proves it).
_LIVE_STAKE_MARKET_ALIASES = {
    "totals": frozenset({"total_over", "total_under"}),
    "total": frozenset({"total_over", "total_under"}),
    "moneyline": frozenset({"w1", "w2", "draw"}),
    "1x2": frozenset({"w1", "w2", "draw"}),
}
_LIVE_STAKE_MARKETS_RAW = os.getenv(
    "NEURALBET_LIVE_STAKE_MARKETS",
    "totals,w1,w2",
).strip().lower()
if _LIVE_STAKE_MARKETS_RAW in ("", "*", "all"):
    LIVE_STAKE_MARKETS: frozenset[str] | None = None
else:
    _market_parts: set[str] = set()
    for part in _LIVE_STAKE_MARKETS_RAW.split(","):
        token = part.strip().lower()
        if not token:
            continue
        if token in _LIVE_STAKE_MARKET_ALIASES:
            _market_parts.update(_LIVE_STAKE_MARKET_ALIASES[token])
        else:
            _market_parts.add(token)
    LIVE_STAKE_MARKETS = frozenset(_market_parts) if _market_parts else None

_FACTOR_TO_LIVE_MARKET = {
    921: "w1",
    DRAW_FACTOR_ID: "draw",
    923: "w2",
    **{fid: "total_over" for fid in TOTAL_OVER_IDS},
    **{fid: "total_under" for fid in TOTAL_UNDER_IDS},
}


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


# ---------------------------------------------------------------------------
# Per-sport Brier gate — last backtest writes a JSON allowlist; live + next
# backtest stake only sports whose model Brier clearly beats the bookmaker.
# File missing / gate off → env ceiling only (no extra restriction).
# ---------------------------------------------------------------------------
BRIER_SPORT_GATE = os.getenv("NEURALBET_BRIER_SPORT_GATE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
BRIER_SPORT_MIN_EVALUATED = int(os.getenv("NEURALBET_BRIER_SPORT_MIN_EVALUATED", "2000"))
BRIER_SPORT_MARGIN = float(os.getenv("NEURALBET_BRIER_SPORT_MARGIN", "0.005"))
LIVE_BRIER_SPORTS_PATH = os.path.join(
    os.getenv("MODEL_DIR", "/app/data/models"),
    "live_stake_brier_sports.json",
)
_brier_sports_lock = threading.Lock()
_brier_sports_cache: tuple[float, Optional[frozenset[str]]] = (-1.0, None)


def _sport_brier_pair(row: dict[str, Any]) -> tuple[Optional[float], Optional[float], int]:
    evaluated = int(row.get("evaluated") or 0)
    prob = row.get("probability") or {}
    current = prob.get("current") if isinstance(prob, dict) else None
    brier = None
    if isinstance(current, dict):
        brier = current.get("brier")
    if brier is None:
        legacy = row.get("current")
        if isinstance(legacy, dict):
            brier = legacy.get("brier")
        else:
            brier = row.get("brier")
    market = row.get("market_brier")
    if market is None and isinstance(prob, dict):
        market = (prob.get("market_raw") or {}).get("brier")
    try:
        brier_f = float(brier) if brier is not None else None
    except (TypeError, ValueError):
        brier_f = None
    try:
        market_f = float(market) if market is not None else None
    except (TypeError, ValueError):
        market_f = None
    return brier_f, market_f, evaluated


def select_brier_stake_sports(
    by_sport: list[dict[str, Any]],
    *,
    min_evaluated: Optional[int] = None,
    margin: Optional[float] = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Sports whose model Brier is at least `margin` below market Brier."""
    floor = BRIER_SPORT_MIN_EVALUATED if min_evaluated is None else int(min_evaluated)
    gap = BRIER_SPORT_MARGIN if margin is None else float(margin)
    selected: list[str] = []
    detail: list[dict[str, Any]] = []
    for row in by_sport or []:
        sport = sport_top(str(row.get("sport") or ""))
        if not sport:
            continue
        brier, market, evaluated = _sport_brier_pair(row)
        enabled = (
            brier is not None
            and market is not None
            and evaluated >= floor
            and brier < (market - gap)
        )
        if enabled:
            selected.append(sport)
        detail.append({
            "sport": sport,
            "evaluated": evaluated,
            "brier": brier,
            "market_brier": market,
            "enabled": enabled,
        })
    selected = sorted(set(selected))
    return selected, detail


def _read_brier_allowlist_unlocked() -> Optional[frozenset[str]]:
    """None = file missing / unreadable (do not extra-restrict)."""
    global _brier_sports_cache
    path = LIVE_BRIER_SPORTS_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        _brier_sports_cache = (-1.0, None)
        return None
    cached_mtime, cached_sports = _brier_sports_cache
    if cached_mtime == mtime:
        return cached_sports
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        sports = payload.get("sports")
        if not isinstance(sports, list):
            allow = frozenset()
        else:
            allow = frozenset(sport_top(str(s)) for s in sports if str(s).strip())
    except (OSError, json.JSONDecodeError, TypeError):
        allow = None
    _brier_sports_cache = (mtime, allow)
    return allow


def brier_stake_sports_override() -> Optional[frozenset[str]]:
    """None = gate off or no file yet. Empty frozenset = stake no sport."""
    if not BRIER_SPORT_GATE:
        return None
    with _brier_sports_lock:
        return _read_brier_allowlist_unlocked()


def effective_live_stake_sports() -> Optional[frozenset[str]]:
    """Sports that currently pass the live sport gate. None = no sport restriction."""
    override = brier_stake_sports_override()
    if LIVE_STAKE_SPORTS is None and override is None:
        return None
    if LIVE_STAKE_SPORTS is None:
        return override
    if override is None:
        return LIVE_STAKE_SPORTS
    return LIVE_STAKE_SPORTS & override


def write_brier_stake_sports(
    sports: list[str],
    *,
    source: str,
    detail: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    global _brier_sports_cache
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "min_evaluated": BRIER_SPORT_MIN_EVALUATED,
        "margin": BRIER_SPORT_MARGIN,
        "sports": sorted({sport_top(s) for s in sports if sport_top(s)}),
        "detail": detail or [],
    }
    path = LIVE_BRIER_SPORTS_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with _brier_sports_lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        _brier_sports_cache = (-1.0, None)
    return payload


def clear_brier_stake_sports() -> None:
    path = LIVE_BRIER_SPORTS_PATH
    with _brier_sports_lock:
        try:
            os.remove(path)
        except OSError:
            pass
        global _brier_sports_cache
        _brier_sports_cache = (-1.0, None)


def update_brier_stake_sports_from_backtest(result: dict[str, Any]) -> dict[str, Any]:
    """Persist Brier winners from a finished backtest. Prefers walk-forward by_sport."""
    wf_rows = result.get("walk_forward_by_sport")
    if wf_rows:
        rows, source = wf_rows, "walk_forward"
    else:
        rows, source = (result.get("by_sport") or []), "overall"
    selected, detail = select_brier_stake_sports(rows)
    return write_brier_stake_sports(selected, source=source, detail=detail)


def in_live_stake_sport(sport_path: Optional[str]) -> bool:
    """Whether live staking / «Активные LIVE» lists include this sport.

    Env `NEURALBET_LIVE_STAKE_SPORTS` is the ceiling. When the Brier gate file
    exists, the sport must also be in that allowlist (last backtest's Brier
    winners). The file is written *after* a backtest scores, so that run itself
    still uses the previous allowlist (no leakage).
    """
    sport = sport_top(sport_path)
    if LIVE_STAKE_SPORTS is not None and sport not in LIVE_STAKE_SPORTS:
        return False
    override = brier_stake_sports_override()
    if override is None:
        return True
    return sport in override


def live_market_family(
    factor_id: Optional[int] = None,
    market_label: Optional[str] = None,
) -> Optional[str]:
    """Canonical live-market family name, or None if unknown."""
    if market_label:
        label = str(market_label).strip().lower()
        if label:
            return label
    if factor_id is None:
        return None
    return _FACTOR_TO_LIVE_MARKET.get(int(factor_id))


def in_live_stake_market(
    factor_id: Optional[int] = None,
    market_label: Optional[str] = None,
) -> bool:
    """Whether live staking includes this market family. Unknown factor fails open
    when no label is given (same spirit as empty market-support cache)."""
    if LIVE_STAKE_MARKETS is None:
        return True
    family = live_market_family(factor_id=factor_id, market_label=market_label)
    if family is None:
        return True
    return family in LIVE_STAKE_MARKETS


def live_gate_skip_reason(
    coeff: float,
    expected_roi: float,
    support_count: Optional[int] = None,
    sport_path: Optional[str] = None,
    factor_id: Optional[int] = None,
    market_label: Optional[str] = None,
) -> Optional[str]:
    """Why this candidate is not staked, or None if it clears every live gate.
    `support_count is None` means 'don't check' (empty cache fails open)."""
    if sport_path is not None and not in_live_stake_sport(sport_path):
        return "sport"
    if (factor_id is not None or market_label is not None) and not in_live_stake_market(
        factor_id=factor_id, market_label=market_label,
    ):
        return "market"
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
    sport_path: Optional[str] = None,
    factor_id: Optional[int] = None,
    market_label: Optional[str] = None,
) -> bool:
    return live_gate_skip_reason(
        coeff,
        expected_roi,
        support_count,
        sport_path,
        factor_id=factor_id,
        market_label=market_label,
    ) is None


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
