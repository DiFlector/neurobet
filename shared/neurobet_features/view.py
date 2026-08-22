"""Single trajectory → model-input path for live, training, and backtest.

Every caller builds the same sample dict (odds/score/ts/timer/overround
sequences + sport/market/teams) and calls `build_model_input`. Cutoff is
the only mode switch:

- train: random prefix among decision-eligible moments (in-band coeff, min history)
- val/backtest: deterministic or replay policy — not closing 1.01 ticks
- serve: all snapshots we have *now* (live history is already truncated)
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neurobet_filters import (
    MIN_BET_COEFF,
    MAX_BET_COEFF,
    TOTAL_FACTOR_IDS,
    parse_total_line,
    sport_top,
)

from .no_vig import no_vig_probability
from .overround import OVERROUND_SANE_MAX, OVERROUND_UNKNOWN
from .parse import (
    pack_timer_entry,
    parse_score_sum,
    parse_timer,
    period_index,
    unpack_timer_entry,
)
from .player_stats import (
    TEAM_STATS_DIM,
    TEAM_STATS_FEATURE_NAMES,
    lookup_team_stats_vector,
    unknown_stats_vector,
)
from .team_form import FORM_UNKNOWN, lookup_team_form
from .vocab import market_family_index, sport_index, team_index

SEQ_LEN = 10
GRU_INPUT_DIM = 10
TIME_NORM_SECONDS = float(os.getenv("NEURALBET_TIME_NORM_SECONDS", "7200.0"))
MATCH_TIME_NORM_SECONDS = float(os.getenv("NEURALBET_MATCH_TIME_NORM_SECONDS", "5400.0"))
MATCH_TIME_UNKNOWN = -1.0
VAL_CUTOFF_SEED = int(os.getenv("NEURALBET_VAL_CUTOFF_SEED", "42"))
TRAIN_MIN_HISTORY = int(os.getenv("NEURALBET_TRAIN_MIN_HISTORY", "3"))
LGB_DISABLE_TEAM_FEATURES = os.getenv("NEURALBET_LGB_DISABLE_TEAM_FEATURES", "0").strip().lower() in (
    "1", "true", "yes", "on",
)

# Clamp sport-raw score_diff into roughly [-1, 1] so 3 football goals and
# 3 NBA points are not the same number to the GRU.
_MATCH_SCORE_SCALE = {
    "футбол": 4.0,
    "баскетбол": 25.0,
    "волейбол": 3.0,
    "теннис": 2.0,
    "настольный теннис": 3.0,
}
_POINT_SCORE_SCALE = {
    "футбол": 4.0,
    "баскетбол": 25.0,
    "волейбол": 25.0,
    "теннис": 6.0,
    "настольный теннис": 11.0,
}
_SUM_SCORE_SCALE = {
    "футбол": 6.0,
    "баскетбол": 300.0,
    "волейбол": 60.0,
    "теннис": 20.0,
    "настольный теннис": 40.0,
}
_TYPICAL_TOTAL_LINE = {
    "футбол": 2.5,
    "баскетбол": 180.0,
    "волейбол": 45.0,
    "теннис": 9.5,
    "настольный теннис": 19.5,
}
_DEFAULT_MATCH_SCALE = 5.0
_DEFAULT_POINT_SCALE = 15.0
_DEFAULT_SUM_SCALE = 20.0
_DEFAULT_LINE = 10.0

# Pad token: log(1.01)≈0.01, zeros, unknown match_time, pad_mask=0.
_PAD_LOG_COEFF = math.log(1.01)

LGB_FEATURE_NAMES = [
    "coefficient",
    "initial_coefficient",
    "drop_ratio",
    "volatility",
    "samples_count",
    "factor_id",
    "score_diff",
    "score_sum",
    "line_remaining",
    "period_point_sum",
    "sport_idx",
    "team1_idx",
    "team2_idx",
    "team1_form",
    "team2_form",
    "overround",
    "no_vig_prob",
    "total_line",
    "period_index",
    "set_point_diff",
    *TEAM_STATS_FEATURE_NAMES,
]
KB_CONTEXT_DIM = TEAM_STATS_DIM
LGB_CATEGORICAL_FEATURES = [
    "factor_id",
    "sport_idx",
    "period_index",
    "team1_idx",
    "team2_idx",
]

# Optional rolling form cache — populated by pipeline before LGB refit / inference.
_TEAM_FORM_CACHE: Optional[Dict[Tuple[int, str, int], float]] = None
# Optional team/player match-stats KB — same refresh points as form (+ inference ensure).
_TEAM_STATS_CACHE: Optional[Dict[str, Any]] = None


def set_team_form_cache(cache: Optional[Dict[Tuple[int, str, int], float]]) -> None:
    global _TEAM_FORM_CACHE
    _TEAM_FORM_CACHE = cache


def set_team_stats_cache(cache: Optional[Dict[str, Any]]) -> None:
    global _TEAM_STATS_CACHE
    _TEAM_STATS_CACHE = cache


def kb_context_vector(view: Mapping[str, Any]) -> List[float]:
    """Fixed-length team-stats KB vector for the GRU context concat."""
    if LGB_DISABLE_TEAM_FEATURES:
        return unknown_stats_vector()
    raw = view.get("team_stats_vec")
    if isinstance(raw, (list, tuple)) and len(raw) == TEAM_STATS_DIM:
        return [float(x) for x in raw]
    out = unknown_stats_vector()
    for i, name in enumerate(TEAM_STATS_FEATURE_NAMES):
        if name in view and view[name] is not None:
            out[i] = float(view[name])
    return out


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        if key in row:
            return row[key]
        try:
            if key in row.keys():
                return row[key]
        except Exception:
            pass
        return default
    try:
        if key in row.keys():
            return row[key]
    except Exception:
        pass
    return default


def _loads_list(raw: Any) -> list:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _scale(raw: Optional[float], scale: float) -> float:
    if raw is None or scale <= 0:
        return 0.0
    return max(-1.0, min(1.0, float(raw) / scale))


def scale_match_score(raw: Optional[float], sport_path: Optional[str]) -> float:
    return _scale(raw, _MATCH_SCORE_SCALE.get(sport_top(sport_path), _DEFAULT_MATCH_SCALE))


def scale_score_sum(raw: Optional[float], sport_path: Optional[str]) -> float:
    return _scale(raw, _SUM_SCORE_SCALE.get(sport_top(sport_path), _DEFAULT_SUM_SCALE))


def scale_set_points(raw: Optional[float], sport_path: Optional[str]) -> float:
    return _scale(raw, _POINT_SCORE_SCALE.get(sport_top(sport_path), _DEFAULT_POINT_SCALE))


def scale_total_line(line: Optional[float], sport_path: Optional[str]) -> float:
    if line is None:
        return 0.0
    typical = _TYPICAL_TOTAL_LINE.get(sport_top(sport_path), _DEFAULT_LINE)
    if typical <= 0:
        return 0.0
    return max(0.0, min(2.0, float(line) / typical))


def scale_line_remaining(remaining: Optional[float], sport_path: Optional[str]) -> float:
    if remaining is None:
        return 0.0
    typical = _TYPICAL_TOTAL_LINE.get(sport_top(sport_path), _DEFAULT_LINE)
    if typical <= 0:
        return 0.0
    return max(-1.0, min(1.0, float(remaining) / typical))


def scale_overround(overround: Optional[float]) -> float:
    if overround is None or overround <= 1.0 or overround > OVERROUND_SANE_MAX:
        return 0.0
    return max(0.0, min(1.0, (float(overround) - 1.0) / 0.15))


def _period_point_sum_from_timer(timer_raw: Any) -> Optional[float]:
    parsed = parse_timer(timer_raw)
    if parsed.set_point_diff is None:
        return None
    # Racket strings encode current-set points as diff; sum ≈ |diff| when unknown.
    return abs(float(parsed.set_point_diff))


def _score_sum_at_index(
    score_seq: Sequence[Any],
    timer_seq: Sequence[Any],
    idx: int,
    fallback_diff: float,
) -> float:
    raw = score_seq[idx] if idx < len(score_seq) else fallback_diff
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        return float(raw[1])
    if isinstance(raw, dict) and "s" in raw:
        return float(raw["s"])
    if isinstance(raw, (int, float)) and raw >= 0 and idx < len(timer_seq):
        pts = _period_point_sum_from_timer(timer_seq[idx])
        if pts is not None:
            return float(pts)
    if isinstance(raw, (int, float)):
        # Legacy diff-only rows — cannot recover true sum; use 0 (model learns from coeff).
        return 0.0
    return 0.0


def _score_diff_at_index(score_seq: Sequence[Any], idx: int, fallback: float) -> float:
    raw = score_seq[idx] if idx < len(score_seq) else fallback
    if isinstance(raw, (list, tuple)) and len(raw) >= 1:
        return float(raw[0])
    if isinstance(raw, dict) and "d" in raw:
        return float(raw["d"])
    if isinstance(raw, (int, float)):
        return float(raw)
    return float(fallback)


def _eligible_cutoff_indices(n: int, coeffs: Sequence[float], mode: str) -> List[int]:
    """0-based indices where a live-style decision could have been taken."""
    if n <= 0:
        return []
    min_i = min(TRAIN_MIN_HISTORY - 1, n - 1)
    out: List[int] = []
    for i in range(n):
        if i < min_i:
            continue
        c = coeffs[i] if i < len(coeffs) else None
        if c is None:
            continue
        cf = float(c)
        if mode == "train":
            if MIN_BET_COEFF <= cf <= MAX_BET_COEFF:
                out.append(i)
        elif mode in ("val", "backtest"):
            if MIN_BET_COEFF <= cf <= MAX_BET_COEFF:
                out.append(i)
        else:
            out.append(i)
    if not out and n >= TRAIN_MIN_HISTORY:
        # Fallback: at least skip closing 1.01-only tail when possible.
        for i in range(min_i, n):
            c = coeffs[i] if i < len(coeffs) else None
            if c is not None and float(c) >= MIN_BET_COEFF:
                out.append(i)
    if not out:
        out = list(range(max(min_i, 0), n))
    return out


def _deterministic_cutoff(key: Tuple[Any, ...], eligible: List[int]) -> int:
    if not eligible:
        return 0
    digest = hashlib.md5(f"{key}:{VAL_CUTOFF_SEED}".encode()).hexdigest()
    pick = int(digest, 16) % len(eligible)
    return eligible[pick]


def cutoff_index(
    n: int,
    mode: str,
    coeffs: Sequence[float],
    *,
    sample_key: Optional[Tuple[Any, ...]] = None,
) -> int:
    """How many leading snapshots the model is allowed to see (1-based length)."""
    if n <= 0:
        return 0
    if n == 1:
        return 1
    if mode == "train":
        eligible = _eligible_cutoff_indices(n, coeffs, "train")
        if not eligible:
            return random.randint(min(TRAIN_MIN_HISTORY, n), n)
        return random.choice(eligible) + 1
    if mode in ("val", "backtest"):
        eligible = _eligible_cutoff_indices(n, coeffs, mode)
        if mode == "backtest":
            return (eligible[-1] + 1) if eligible else n
        key = sample_key or ()
        return _deterministic_cutoff(key, eligible) + 1
    return n


def row_to_sample(row: Any) -> Dict[str, Any]:
    """Parse a finished_bets JOIN finished_events row into the sample dict
    `build_model_input` consumes. Shared by pipeline training fetch and backtest."""
    odds_seq = _loads_list(_row_get(row, "odds_seq_json"))
    score_seq = _loads_list(_row_get(row, "score_seq_json"))
    score_sum_seq = _loads_list(_row_get(row, "score_sum_seq_json"))
    ts_seq = _loads_list(_row_get(row, "ts_seq_json"))
    timer_seq = _loads_list(_row_get(row, "timer_seq_json"))
    overround_seq = _loads_list(_row_get(row, "overround_seq_json"))
    return {
        "is_win": _row_get(row, "is_win"),
        "odds_seq": odds_seq,
        "score_seq": score_seq,
        "score_sum_seq": score_sum_seq,
        "ts_seq": ts_seq,
        "timer_seq": timer_seq,
        "overround_seq": overround_seq,
        "score_diff_at_bet": _row_get(row, "score_diff_at_bet") or 0,
        "factor_id": _row_get(row, "factor_id"),
        "parameter": _row_get(row, "parameter") or "",
        "market_prefix": _row_get(row, "market_prefix") or "",
        "label": _row_get(row, "label") or "",
        "event_id": _row_get(row, "event_id"),
        "sport_path": _row_get(row, "sport_path") or "",
        "team_1": _row_get(row, "team_1") or "",
        "team_2": _row_get(row, "team_2") or "",
        "overround_close": _row_get(row, "overround_close"),
        "final_coefficient": _row_get(row, "final_coefficient"),
        "predicted_win_probability": _row_get(row, "predicted_win_probability"),
        "predicted_win": _row_get(row, "predicted_win"),
        "trained_count": _row_get(row, "trained_count") or 0,
        "finished_at": _row_get(row, "finished_at"),
        "_key": (
            _row_get(row, "event_id"),
            _row_get(row, "factor_id"),
            _row_get(row, "parameter"),
            _row_get(row, "market_prefix"),
        ),
    }


def live_sample(
    *,
    coeffs: Sequence[float],
    score_diffs: Sequence[int],
    score_sums: Optional[Sequence[int]] = None,
    timestamps: Sequence[Optional[float]],
    timer_raws: Sequence[Any],
    factor_id: int,
    parameter: str,
    market_prefix: str,
    sport_path: str,
    team_1: str,
    team_2: str,
    overround: Optional[float],
    event_id: Any = None,
) -> Dict[str, Any]:
    """Build the same sample dict from a live odds_history trajectory."""
    packed_timer = []
    for raw in timer_raws:
        parsed = parse_timer(raw)
        packed_timer.append(pack_timer_entry(parsed.match_time_seconds, parsed.set_point_diff))
    n = len(coeffs)
    overround_seq: List[Optional[float]] = [None] * n
    if n and overround is not None:
        overround_seq[-1] = overround
    score_sum_seq = list(score_sums) if score_sums is not None else [abs(d) for d in score_diffs]
    return {
        "odds_seq": list(coeffs),
        "score_seq": list(score_diffs),
        "score_sum_seq": score_sum_seq,
        "ts_seq": list(timestamps),
        "timer_seq": packed_timer,
        "overround_seq": overround_seq,
        "score_diff_at_bet": score_diffs[0] if score_diffs else 0,
        "factor_id": factor_id,
        "parameter": parameter or "",
        "market_prefix": market_prefix or "",
        "event_id": event_id,
        "sport_path": sport_path or "",
        "team_1": team_1 or "",
        "team_2": team_2 or "",
        "overround": overround,
        "is_win": None,
    }


def build_model_input(sample: Mapping[str, Any], mode: str = "serve") -> Optional[Dict[str, Any]]:
    odds_seq = list(sample.get("odds_seq") or [])
    if not odds_seq:
        return None
    n = len(odds_seq)
    fallback = sample.get("score_diff_at_bet", 0)
    score_seq = list(sample.get("score_seq") or [])
    score_sum_seq = list(sample.get("score_sum_seq") or [])
    if len(score_seq) != n:
        score_seq = [fallback] * n
    if len(score_sum_seq) != n:
        score_sum_seq = [0] * n
    ts_seq = list(sample.get("ts_seq") or [])
    if len(ts_seq) != n:
        ts_seq = [None] * n
    timer_seq = list(sample.get("timer_seq") or [])
    if len(timer_seq) != n:
        timer_seq = [None] * n
    overround_seq = list(sample.get("overround_seq") or [])
    if len(overround_seq) != n:
        overround_seq = [None] * n

    raw_key = sample.get("_key")
    sample_key = tuple(raw_key) if raw_key else (
        sample.get("event_id"),
        sample.get("factor_id"),
        sample.get("parameter"),
        sample.get("market_prefix"),
    )
    cut = cutoff_index(n, mode, odds_seq, sample_key=sample_key)
    if cut <= 0:
        return None

    odds = [float(c) for c in odds_seq[:cut]]
    scores = score_seq[:cut]
    sums = score_sum_seq[:cut]
    ts = ts_seq[:cut]
    timers = [unpack_timer_entry(v) for v in timer_seq[:cut]]
    overs = overround_seq[:cut]

    sport_path = sample.get("sport_path") or ""
    parameter = str(sample.get("parameter") or "")
    prefix = str(sample.get("market_prefix") or "")
    factor_id = int(sample.get("factor_id") or 0)
    line = parse_total_line(parameter)
    is_total = factor_id in TOTAL_FACTOR_IDS

    step_pairs = []
    for i in range(cut):
        raw_diff = _score_diff_at_index(scores, i, fallback)
        if sums[i] if i < len(sums) else None:
            raw_sum = float(sums[i])
        else:
            raw_sum = _score_sum_at_index(scores, timer_seq, i, fallback)
        step_pairs.append((
            odds[i],
            raw_diff,
            raw_sum,
            ts[i],
            timers[i].match_time_seconds,
            timers[i].set_point_diff,
        ))

    last_points = timers[-1].set_point_diff if timers else None
    period_pt_sum = _period_point_sum_from_timer(timer_seq[cut - 1] if cut - 1 < len(timer_seq) else None)
    if period_pt_sum is None and last_points is not None:
        period_pt_sum = abs(float(last_points))

    overround = overs[-1] if overs else None
    if overround is None and mode == "serve":
        overround = sample.get("overround")
        if overround is None:
            overround = sample.get("overround_close")

    current_coeff = odds[-1]
    initial_coeff = odds[0]
    raw_sum = step_pairs[-1][2]
    line_remaining = (float(line) - raw_sum) if (is_total and line is not None) else None
    nv_prob = no_vig_probability(current_coeff, overround)

    team1 = sample.get("team_1") or ""
    team2 = sample.get("team_2") or ""
    team1_form = sample.get("team1_form_asof")
    team2_form = sample.get("team2_form_asof")
    if team1_form is None:
        team1_form = lookup_team_form(_TEAM_FORM_CACHE, team1, sport_path, factor_id)
    if team2_form is None:
        team2_form = lookup_team_form(_TEAM_FORM_CACHE, team2, sport_path, factor_id)

    team_stats_vec = sample.get("team_stats_asof")
    if not isinstance(team_stats_vec, (list, tuple)) or len(team_stats_vec) != TEAM_STATS_DIM:
        team_stats_vec = lookup_team_stats_vector(
            _TEAM_STATS_CACHE, team1, team2, sport_path,
        )
    else:
        team_stats_vec = [float(x) for x in team_stats_vec]
    stats_fields = {
        name: float(team_stats_vec[i]) for i, name in enumerate(TEAM_STATS_FEATURE_NAMES)
    }

    return {
        "step_pairs": step_pairs,
        "current_coeff": current_coeff,
        "initial_coeff": initial_coeff,
        "min_coefficient": min(odds),
        "max_coefficient": max(odds),
        "samples_count": len(odds),
        "coefficient": current_coeff,
        "initial_coefficient": initial_coeff,
        "factor_id": factor_id,
        "parameter": parameter,
        "market_prefix": prefix,
        "sport_path": sport_path,
        "team_1": team1,
        "team_2": team2,
        "overround": overround,
        "overround_close": overround,
        "no_vig_prob": nv_prob,
        "total_line": float(line) if line is not None else 0.0,
        "total_line_norm": scale_total_line(line, sport_path),
        "period_index": period_index(prefix),
        "set_point_diff": scale_set_points(last_points, sport_path),
        "period_point_sum": scale_set_points(period_pt_sum, sport_path),
        "score_diff": scale_match_score(step_pairs[-1][1], sport_path),
        "score_sum": scale_score_sum(raw_sum, sport_path),
        "line_remaining": scale_line_remaining(line_remaining, sport_path),
        "raw_score_diff": step_pairs[-1][1],
        "raw_score_sum": raw_sum,
        "sport_idx": sport_index(sport_path),
        "market_idx": market_family_index(factor_id),
        "team1_idx": team_index(team1),
        "team2_idx": team_index(team2),
        "team1_form": team1_form,
        "team2_form": team2_form,
        "team_stats_vec": team_stats_vec,
        **stats_fields,
        "sport": (sport_path.split("/")[0].strip() or "Другое"),
        "target": None if sample.get("is_win") is None else float(sample["is_win"]),
        "is_win": sample.get("is_win"),
        "conflict_key": sample.get("event_id"),
        "event_id": sample.get("event_id"),
        "label": sample.get("label") or "",
        "trained_count": int(sample.get("trained_count") or 0),
        "finished_at": sample.get("finished_at"),
        "_key": sample_key,
    }


def build_gru_sequence(
    step_pairs: Sequence[Tuple],
    sport_path: Optional[str] = None,
    *,
    total_line: Optional[float] = None,
    period_index: int = 0,
    overround: Optional[float] = None,
) -> List[List[float]]:
    """Fixed-length (SEQ_LEN, GRU_INPUT_DIM) tensor rows.

    Channels: log(coeff), score_diff, score_sum, line_remaining, t_norm,
    match_time, set-point diff, period point sum, period/overround, pad_mask.
    """
    pairs = list(step_pairs) if step_pairs else [(1.5, 0, 0, None, None, None)]
    n_real = len(pairs)
    if n_real > SEQ_LEN:
        pairs = pairs[-SEQ_LEN:]
        n_real = SEQ_LEN
    n_pad = SEQ_LEN - n_real

    timestamps = [(p[3] if len(p) > 3 else None) for p in pairs]
    use_real_time = n_real > 0 and all(t is not None for t in timestamps)
    if use_real_time:
        base = float(timestamps[0])
        t_feats = [
            min(max(float(t) - base, 0.0) / TIME_NORM_SECONDS, 1.0) for t in timestamps
        ]
    else:
        t_feats = [(i / (n_real - 1)) if n_real > 1 else 1.0 for i in range(n_real)]

    period_feat = min(max(float(period_index) / 4.0, 0.0), 1.0)
    or_feat = scale_overround(overround)

    rows: List[List[float]] = []
    for _ in range(n_pad):
        rows.append([_PAD_LOG_COEFF] + [0.0] * (GRU_INPUT_DIM - 2) + [0.0])
    for p, t_norm in zip(pairs, t_feats):
        coeff = float(p[0])
        raw_diff = float(p[1]) if len(p) > 1 else 0.0
        raw_sum = float(p[2]) if len(p) > 2 else 0.0
        mt = p[4] if len(p) > 4 else None
        pts = p[5] if len(p) > 5 else None
        match_time = (
            min(max(float(mt), 0.0) / MATCH_TIME_NORM_SECONDS, 1.0)
            if mt is not None else MATCH_TIME_UNKNOWN
        )
        period_pts = abs(float(pts)) if pts is not None else 0.0
        line_rem = (
            scale_line_remaining(float(total_line) - raw_sum, sport_path)
            if total_line is not None else 0.0
        )
        rows.append([
            math.log(max(coeff, 1.01)),
            scale_match_score(raw_diff, sport_path),
            scale_score_sum(raw_sum, sport_path),
            line_rem,
            float(t_norm),
            match_time,
            scale_set_points(pts, sport_path),
            scale_set_points(period_pts, sport_path),
            max(period_feat, or_feat),
            1.0,
        ])
    return rows


def lgb_feature_row(view: Mapping[str, Any]) -> List[float]:
    coeff = float(view.get("current_coeff") or view.get("coefficient") or 1.5)
    initial = float(view.get("initial_coeff") or view.get("initial_coefficient") or coeff)
    min_c = view.get("min_coefficient")
    max_c = view.get("max_coefficient")
    if min_c is None or max_c is None:
        pairs = view.get("step_pairs") or []
        coeffs = [float(p[0]) for p in pairs] or [coeff]
        min_c = min(coeffs)
        max_c = max(coeffs)
    drop_ratio = (initial - coeff) / initial if initial > 0 else 0.0
    volatility = float(max_c) - float(min_c)
    overround = view.get("overround")
    if overround is None:
        overround = view.get("overround_close")
    samples = view.get("samples_count")
    if samples is None:
        samples = len(view.get("step_pairs") or [])
    return [
        coeff,
        initial,
        drop_ratio,
        volatility,
        float(samples or 0),
        float(int(view.get("factor_id") or 0)),
        float(view.get("score_diff") or 0.0),
        float(view.get("score_sum") or 0.0),
        float(view.get("line_remaining") or 0.0),
        float(view.get("period_point_sum") or 0.0),
        float(view.get("sport_idx") or 0),
        0.0 if LGB_DISABLE_TEAM_FEATURES else float(view.get("team1_idx") or 0),
        0.0 if LGB_DISABLE_TEAM_FEATURES else float(view.get("team2_idx") or 0),
        FORM_UNKNOWN if LGB_DISABLE_TEAM_FEATURES else float(
            view.get("team1_form") if view.get("team1_form") is not None else FORM_UNKNOWN
        ),
        FORM_UNKNOWN if LGB_DISABLE_TEAM_FEATURES else float(
            view.get("team2_form") if view.get("team2_form") is not None else FORM_UNKNOWN
        ),
        float(overround) if overround else OVERROUND_UNKNOWN,
        float(view.get("no_vig_prob") or no_vig_probability(coeff, overround)),
        float(view.get("total_line") or 0.0),
        float(int(view.get("period_index") or 0)),
        float(view.get("set_point_diff") or 0.0),
        *(
            unknown_stats_vector() if LGB_DISABLE_TEAM_FEATURES
            else kb_context_vector(view)
        ),
    ]
