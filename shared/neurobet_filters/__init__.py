"""
Single source of truth for NeuroBet sample/betting filters.

Two layers, on purpose:

- Universe (sport / factor_id / total line) — training, live inference, backtest
  fetch, UI, and stats. Win-head still sees every row inside this universe.
- Live gates (coefficient band, min EV, min market support, live sports / markets) —
  staking real money, the "win" LIVE list, backtest "would bet", training
  bankroll/tuner. Thin markets and 1.0–1.1 shorts stay in the gradient; they just
  never get a stake. Money band is 1.1–2.0, plus 2.0–2.5 when calibrated p ≥ 90%.

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
MIN_BET_COEFF = float(os.getenv("NEURALBET_MIN_BET_COEFF", "1.1"))
MAX_BET_COEFF = float(os.getenv("NEURALBET_MAX_BET_COEFF", "2.0"))
# Tail above MAX: stake only when calibrated p is huge. Training still caps at MAX.
MAX_BET_COEFF_HIGH_P = float(os.getenv("NEURALBET_MAX_BET_COEFF_HIGH_P", "2.5"))
HIGH_P_STAKE = float(os.getenv("NEURALBET_HIGH_P_STAKE", "0.90"))
# Steam: falling coeff (market p up) nudges calibrated p up; rising coeff nudges down.
# 0 = off. 0.15 ≈ +1.5pp when the price shortens 10%.
COEFF_MOVE_P_WEIGHT = float(os.getenv("NEURALBET_COEFF_MOVE_P_WEIGHT", "0.15"))
MIN_BET_EDGE_PCT = float(os.getenv("NEURALBET_MIN_BET_EDGE_PCT", "5.0"))
W1_FACTOR_ID = 921
W2_FACTOR_ID = 923
MIN_MARKET_SUPPORT = int(os.getenv("NEURALBET_MIN_MARKET_SUPPORT", "150"))
# After sibling renorm, pull calibrated p toward 1/coeff before EV.
# 0.25: a 61.6% call at 1.73 (market ~57.8%) becomes ~60.7% and often drops below 5% EV.
MARKET_SHRINK = float(os.getenv("NEURALBET_MARKET_SHRINK", "0.25"))

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
    """Standard live band (1.1–2.0). Training / decision-head masks use this, not the high-p tail."""
    return MIN_BET_COEFF <= coeff <= MAX_BET_COEFF


def _prob_01(win_p: Any) -> Optional[float]:
    """Accept 0–1 or 0–100 calibrated p."""
    try:
        if win_p is None:
            return None
        v = float(win_p)
    except (TypeError, ValueError):
        return None
    if v > 1.0:
        v = v / 100.0
    return min(max(v, 0.0), 1.0)


def adjust_p_for_coeff_move(
    p: float,
    current_coeff: Any,
    initial_coeff: Any = None,
    weight: Optional[float] = None,
) -> float:
    """If the price shortened, market p rose — bump model p. If it drifted out, cut p.

    Relative move is clipped to ±50% so a 3.0→1.2 collapse cannot dominate the model.
    """
    w = COEFF_MOVE_P_WEIGHT if weight is None else float(weight)
    try:
        out = float(p)
    except (TypeError, ValueError):
        return 0.5
    if w <= 0:
        return min(max(out, 0.01), 0.99)
    try:
        cur = float(current_coeff)
        ini = float(initial_coeff) if initial_coeff is not None else None
    except (TypeError, ValueError):
        return min(max(out, 0.01), 0.99)
    if ini is None or ini <= 1.0 or cur <= 1.0:
        return min(max(out, 0.01), 0.99)
    move = (ini - cur) / ini
    move = min(max(move, -0.5), 0.5)
    return min(max(out + w * move, 0.01), 0.99)


def coeff_ok_for_stake(coeff: float, win_p: Any = None) -> bool:
    """Money band: 1.1–2.0, or 2.0–2.5 when calibrated p ≥ HIGH_P_STAKE."""
    try:
        c = float(coeff)
    except (TypeError, ValueError):
        return False
    if MIN_BET_COEFF <= c <= MAX_BET_COEFF:
        return True
    p = _prob_01(win_p)
    if p is None:
        return False
    return MAX_BET_COEFF < c <= MAX_BET_COEFF_HIGH_P and p >= HIGH_P_STAKE


def score_1x2_prior(
    factor_id: Any,
    score_1: Any,
    score_2: Any,
) -> Optional[int]:
    """Hard 1X2 call from the live score. None = no prior (tied or not 1X2).

    At 1:0, П1 is the leader (1), X and П2 are already losing (0). At 0:0
    there is no prior — do not treat the draw as currently winning.
    """
    try:
        fid = int(factor_id)
    except (TypeError, ValueError):
        return None
    if fid not in (W1_FACTOR_ID, W2_FACTOR_ID, DRAW_FACTOR_ID):
        return None
    try:
        if score_1 is None or score_2 is None:
            return None
        s1, s2 = int(score_1), int(score_2)
    except (TypeError, ValueError):
        return None
    if s1 == s2:
        return None
    home_leads = s1 > s2
    if fid == W1_FACTOR_ID:
        return 1 if home_leads else 0
    if fid == W2_FACTOR_ID:
        return 0 if home_leads else 1
    return 0


def outcome_will_win(
    predicted_win: Any,
    coefficient: Any,
    *,
    factor_id: Any = None,
    score_1: Any = None,
    score_2: Any = None,
    win_probability: Any = None,
    initial_coefficient: Any = None,
) -> Optional[int]:
    """Outcome call for UI / guess-rate / stake eligibility — not the EV flag.

    Order: 1X2 score prior; else p (optionally steam-adjusted) ≥ 50% → 1;
    else p < 50% → 0 («скорее всего не победит»). No p and no prior → None
    (skip — do **not** record as a miss just because the coeff is outside 1.1–2.0
    or EV skipped). ``predicted_win`` is unused (EV/stake bit, not a loss label).
    """
    _ = predicted_win
    prior = score_1x2_prior(factor_id, score_1, score_2)
    if prior is not None:
        return prior
    p = _prob_01(win_probability)
    if p is None:
        return None
    p = adjust_p_for_coeff_move(p, coefficient, initial_coefficient)
    return 1 if p >= 0.5 else 0


def outcome_will_win_sql(
    predicted_expr: str = "h.predicted_win",
    coeff_expr: str = "COALESCE(h.final_coefficient, h.initial_coefficient)",
    *,
    factor_expr: str = "h.factor_id",
    score1_expr: str = "e.score_1",
    score2_expr: str = "e.score_2",
    p_expr: str = "h.predicted_win_probability",
) -> str:
    """SQL twin of ``outcome_will_win`` (1X2 prior + p≥50% → 1/0; else NULL skip).

    Steam is applied at inference (sibling) and stored in ``p_expr`` — do not
    apply it again here or history would double-count.
    """
    del predicted_expr, coeff_expr  # EV / band are not loss labels
    w1, w2, draw = int(W1_FACTOR_ID), int(W2_FACTOR_ID), int(DRAW_FACTOR_ID)
    p01 = f"(CASE WHEN {p_expr} > 1 THEN {p_expr} / 100.0 ELSE {p_expr} END)"
    return (
        f"(CASE "
        f"WHEN {factor_expr} IN ({w1}, {w2}, {draw}) "
        f"AND {score1_expr} IS NOT NULL AND {score2_expr} IS NOT NULL "
        f"AND {score1_expr} <> {score2_expr} THEN "
        f"CASE WHEN {factor_expr} = {w1} AND {score1_expr} > {score2_expr} THEN 1 "
        f"WHEN {factor_expr} = {w2} AND {score2_expr} > {score1_expr} THEN 1 "
        f"ELSE 0 END "
        f"WHEN {p_expr} IS NOT NULL AND {p01} >= 0.5 THEN 1 "
        f"WHEN {p_expr} IS NOT NULL AND {p01} < 0.5 THEN 0 "
        f"ELSE NULL END)"
    )


def shrink_p_toward_market(
    p: float,
    coeff: float,
    shrink: Optional[float] = None,
) -> float:
    """Convex mix of model p and bookmaker-implied 1/coeff. Used on live, bot, backtest."""
    s = MARKET_SHRINK if shrink is None else float(shrink)
    if s <= 0.0:
        return float(p)
    s = min(max(s, 0.0), 1.0)
    c = float(coeff or 0.0)
    market = min(max(1.0 / c, 0.01), 0.99) if c > 1.0 else 0.99
    mixed = (1.0 - s) * float(p) + s * market
    return min(max(mixed, 0.01), 0.99)


def in_verdict_train_band(coeff: float) -> bool:
    """Decision-head training drops longs at/above MAX_BET_COEFF (a free 'will lose'
    label that drowns the residual). Inclusive bet-band still stakes exactly MAX."""
    return coeff < MAX_BET_COEFF


# ---------------------------------------------------------------------------
# Per-sport Brier gate — last backtest writes a JSON allowlist; live + next
# backtest stake only sports whose model Brier clearly beats the bookmaker
# and (by default) whose walk-forward ROI CI lo is positive on enough bets.
# File missing / gate off → env ceiling only (no extra restriction).
# ---------------------------------------------------------------------------
BRIER_SPORT_GATE = os.getenv("NEURALBET_BRIER_SPORT_GATE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
BRIER_SPORT_MIN_EVALUATED = int(os.getenv("NEURALBET_BRIER_SPORT_MIN_EVALUATED", "2000"))
BRIER_SPORT_MARGIN = float(os.getenv("NEURALBET_BRIER_SPORT_MARGIN", "0.005"))
# Extra stake filter on top of Brier: sport must have enough bets and roi_pct_lo > 0
# (walk-forward CI). Basketball can beat market Brier while its ROI CI is negative.
BRIER_SPORT_MIN_BETS = int(os.getenv("NEURALBET_BRIER_SPORT_MIN_BETS", "40"))
BRIER_SPORT_REQUIRE_ROI_LO = os.getenv(
    "NEURALBET_BRIER_SPORT_REQUIRE_ROI_LO", "1",
).strip().lower() not in ("0", "false", "no", "off")
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


def _sport_stake_ci(row: dict[str, Any]) -> tuple[Optional[float], int]:
    stake = ((row.get("stake_policy") or {}).get("current")) or {}
    bets = stake.get("flat_bets")
    if bets is None:
        bets = stake.get("bets")
    if bets is None:
        bets = row.get("bets")
    if bets is None:
        legacy = row.get("current")
        if isinstance(legacy, dict):
            bets = legacy.get("bets")
    try:
        bets_i = int(bets or 0)
    except (TypeError, ValueError):
        bets_i = 0
    lo = stake.get("roi_pct_lo")
    if lo is None:
        lo = row.get("roi_pct_lo")
    try:
        lo_f = float(lo) if lo is not None else None
    except (TypeError, ValueError):
        lo_f = None
    return lo_f, bets_i


def select_brier_stake_sports(
    by_sport: list[dict[str, Any]],
    *,
    min_evaluated: Optional[int] = None,
    margin: Optional[float] = None,
    min_bets: Optional[int] = None,
    require_roi_lo: Optional[bool] = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Sports whose model Brier beats market by `margin` and (by default) whose
    walk-forward ROI CI lo is positive on enough bets."""
    floor = BRIER_SPORT_MIN_EVALUATED if min_evaluated is None else int(min_evaluated)
    gap = BRIER_SPORT_MARGIN if margin is None else float(margin)
    bets_floor = BRIER_SPORT_MIN_BETS if min_bets is None else int(min_bets)
    need_ci = BRIER_SPORT_REQUIRE_ROI_LO if require_roi_lo is None else bool(require_roi_lo)
    selected: list[str] = []
    detail: list[dict[str, Any]] = []
    for row in by_sport or []:
        sport = sport_top(str(row.get("sport") or ""))
        if not sport:
            continue
        brier, market, evaluated = _sport_brier_pair(row)
        roi_lo, bets = _sport_stake_ci(row)
        brier_ok = (
            brier is not None
            and market is not None
            and evaluated >= floor
            and brier < (market - gap)
        )
        ci_ok = (
            True
            if not need_ci
            else (roi_lo is not None and roi_lo > 0.0 and bets >= bets_floor)
        )
        enabled = bool(brier_ok and ci_ok)
        if enabled:
            selected.append(sport)
        detail.append({
            "sport": sport,
            "evaluated": evaluated,
            "brier": brier,
            "market_brier": market,
            "bets": bets,
            "roi_pct_lo": roi_lo,
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
        "min_bets": BRIER_SPORT_MIN_BETS,
        "require_roi_lo": BRIER_SPORT_REQUIRE_ROI_LO,
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
    winners with positive ROI CI lo). The file is written *after* a backtest
    scores, so that run itself still uses the previous allowlist (no leakage).
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
    win_probability: Any = None,
    will_win: Any = None,
) -> Optional[str]:
    """Why this candidate is not staked, or None if it clears every live gate.
    `support_count is None` means 'don't check' (empty cache fails open).
    Stake only from will_win=1; coeff 1.1–2.0 or 2.0–2.5 with p ≥ HIGH_P_STAKE.
    """
    if will_win is not None:
        try:
            if int(will_win) != 1:
                return "will_win"
        except (TypeError, ValueError):
            return "will_win"
    if sport_path is not None and not in_live_stake_sport(sport_path):
        return "sport"
    if (factor_id is not None or market_label is not None) and not in_live_stake_market(
        factor_id=factor_id, market_label=market_label,
    ):
        return "market"
    if not coeff_ok_for_stake(coeff, win_probability):
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
    win_probability: Any = None,
    will_win: Any = None,
) -> bool:
    return live_gate_skip_reason(
        coeff,
        expected_roi,
        support_count,
        sport_path,
        factor_id=factor_id,
        market_label=market_label,
        win_probability=win_probability,
        will_win=will_win,
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


def bet_band_sql(
    coeff_expr: str,
    roi_expr: Optional[str] = None,
    p_expr: Optional[str] = None,
) -> Tuple[str, list]:
    """SQL fragment + bind params for the money band and optional min-EV gate.

    With ``p_expr`` (0–1 or 0–100): also allow MAX < coeff ≤ MAX_HIGH_P when p ≥ HIGH_P.
    """
    if p_expr:
        p01 = f"(CASE WHEN {p_expr} > 1 THEN {p_expr} / 100.0 ELSE {p_expr} END)"
        sql = (
            f" AND (({coeff_expr} >= %s AND {coeff_expr} <= %s)"
            f" OR ({coeff_expr} > %s AND {coeff_expr} <= %s AND {p01} >= %s))"
        )
        params: list = [
            MIN_BET_COEFF, MAX_BET_COEFF,
            MAX_BET_COEFF, MAX_BET_COEFF_HIGH_P, HIGH_P_STAKE,
        ]
    else:
        sql = f" AND {coeff_expr} >= %s AND {coeff_expr} <= %s"
        params = [MIN_BET_COEFF, MAX_BET_COEFF]
    if roi_expr:
        sql += f" AND {roi_expr} >= %s"
        params.append(MIN_BET_EDGE_PCT)
    return sql, params
