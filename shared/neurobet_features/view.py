"""Single trajectory → model-input path for live, training, and backtest.

Every caller builds the same sample dict (odds/score/ts/timer/overround
sequences + sport/market/teams) and calls `build_model_input`. Cutoff is
the only mode switch:

- train: random prefix, so the net never sees the closing tick
- serve: all snapshots we have *now* (live history is already truncated)
- backtest: last snapshot still inside the live coefficient band — the
  moment we could actually have staked — not the 1.01 close
"""
from __future__ import annotations

import json
import math
import os
import random
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neurobet_filters import MIN_BET_COEFF, MAX_BET_COEFF, parse_total_line, sport_top

from .overround import OVERROUND_UNKNOWN
from .parse import (
    pack_timer_entry,
    parse_timer,
    period_index,
    unpack_timer_entry,
)
from .vocab import market_family_index, sport_index, team_index

SEQ_LEN = 10
GRU_INPUT_DIM = 6
TIME_NORM_SECONDS = float(os.getenv("NEURALBET_TIME_NORM_SECONDS", "7200.0"))
MATCH_TIME_NORM_SECONDS = float(os.getenv("NEURALBET_MATCH_TIME_NORM_SECONDS", "5400.0"))
MATCH_TIME_UNKNOWN = -1.0

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
_TYPICAL_TOTAL_LINE = {
    "футбол": 2.5,
    "баскетбол": 180.0,
    "волейбол": 45.0,
    "теннис": 9.5,
    "настольный теннис": 19.5,
}
_DEFAULT_MATCH_SCALE = 5.0
_DEFAULT_POINT_SCALE = 15.0
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
    "sport_idx",
    "team1_idx",
    "team2_idx",
    "overround",
    "total_line",
    "period_index",
    "set_point_diff",
]
LGB_CATEGORICAL_FEATURES = ["factor_id", "sport_idx", "period_index"]


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


def scale_set_points(raw: Optional[float], sport_path: Optional[str]) -> float:
    return _scale(raw, _POINT_SCORE_SCALE.get(sport_top(sport_path), _DEFAULT_POINT_SCALE))


def scale_total_line(line: Optional[float], sport_path: Optional[str]) -> float:
    if line is None:
        return 0.0
    typical = _TYPICAL_TOTAL_LINE.get(sport_top(sport_path), _DEFAULT_LINE)
    if typical <= 0:
        return 0.0
    return max(0.0, min(2.0, float(line) / typical))


def cutoff_index(n: int, mode: str, coeffs: Sequence[float]) -> int:
    """How many leading snapshots the model is allowed to see (1-based length)."""
    if n <= 0:
        return 0
    if n == 1:
        return 1
    if mode == "train":
        return random.randint(min(3, n), n)
    if mode == "backtest":
        in_band = [
            i for i, c in enumerate(coeffs)
            if c is not None and MIN_BET_COEFF <= float(c) <= MAX_BET_COEFF
        ]
        return (in_band[-1] + 1) if in_band else n
    return n


def row_to_sample(row: Any) -> Dict[str, Any]:
    """Parse a finished_bets JOIN finished_events row into the sample dict
    `build_model_input` consumes. Shared by pipeline training fetch and backtest."""
    odds_seq = _loads_list(_row_get(row, "odds_seq_json"))
    score_seq = _loads_list(_row_get(row, "score_seq_json"))
    ts_seq = _loads_list(_row_get(row, "ts_seq_json"))
    timer_seq = _loads_list(_row_get(row, "timer_seq_json"))
    overround_seq = _loads_list(_row_get(row, "overround_seq_json"))
    return {
        "is_win": _row_get(row, "is_win"),
        "odds_seq": odds_seq,
        "score_seq": score_seq,
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
    return {
        "odds_seq": list(coeffs),
        "score_seq": list(score_diffs),
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
    if len(score_seq) != n:
        score_seq = [fallback] * n
    ts_seq = list(sample.get("ts_seq") or [])
    if len(ts_seq) != n:
        ts_seq = [None] * n
    timer_seq = list(sample.get("timer_seq") or [])
    if len(timer_seq) != n:
        timer_seq = [None] * n
    overround_seq = list(sample.get("overround_seq") or [])
    if len(overround_seq) != n:
        overround_seq = [None] * n

    cut = cutoff_index(n, mode, odds_seq)
    if cut <= 0:
        return None

    odds = [float(c) for c in odds_seq[:cut]]
    scores = score_seq[:cut]
    ts = ts_seq[:cut]
    timers = [unpack_timer_entry(v) for v in timer_seq[:cut]]
    overs = overround_seq[:cut]

    step_pairs = [
        (
            odds[i],
            float(scores[i] if scores[i] is not None else 0),
            ts[i],
            timers[i].match_time_seconds,
            timers[i].set_point_diff,
        )
        for i in range(cut)
    ]
    sport_path = sample.get("sport_path") or ""
    parameter = str(sample.get("parameter") or "")
    prefix = str(sample.get("market_prefix") or "")
    factor_id = int(sample.get("factor_id") or 0)
    line = parse_total_line(parameter)
    last_points = timers[-1].set_point_diff if timers else None

    overround = overs[-1] if overs else None
    if overround is None and mode == "serve":
        overround = sample.get("overround")
        if overround is None:
            overround = sample.get("overround_close")

    current_coeff = odds[-1]
    initial_coeff = odds[0]
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
        "team_1": sample.get("team_1") or "",
        "team_2": sample.get("team_2") or "",
        "overround": overround,
        "overround_close": overround,
        "total_line": float(line) if line is not None else 0.0,
        "total_line_norm": scale_total_line(line, sport_path),
        "period_index": period_index(prefix),
        "set_point_diff": scale_set_points(last_points, sport_path),
        "score_diff": scale_match_score(step_pairs[-1][1], sport_path),
        "raw_score_diff": step_pairs[-1][1],
        "sport_idx": sport_index(sport_path),
        "market_idx": market_family_index(factor_id),
        "team1_idx": team_index(sample.get("team_1")),
        "team2_idx": team_index(sample.get("team_2")),
        "sport": (sport_path.split("/")[0].strip() or "Другое"),
        "target": None if sample.get("is_win") is None else float(sample["is_win"]),
        "is_win": sample.get("is_win"),
        "conflict_key": sample.get("event_id"),
        "event_id": sample.get("event_id"),
        "label": sample.get("label") or "",
    }


def build_gru_sequence(step_pairs: Sequence[Tuple], sport_path: Optional[str] = None) -> List[List[float]]:
    """Fixed-length (SEQ_LEN, GRU_INPUT_DIM) tensor rows.

    Channels: log(coeff), scaled match score_diff, t_norm, match_time,
    scaled set-point diff, pad_mask. Short trajectories pad on the LEFT
    with a zero token (pad_mask=0), not a copy of the first live tick.
    """
    pairs = list(step_pairs) if step_pairs else [(1.5, 0, None, None, None)]
    n_real = len(pairs)
    if n_real > SEQ_LEN:
        pairs = pairs[-SEQ_LEN:]
        n_real = SEQ_LEN
    n_pad = SEQ_LEN - n_real

    timestamps = [(p[2] if len(p) > 2 else None) for p in pairs]
    use_real_time = n_real > 0 and all(t is not None for t in timestamps)
    if use_real_time:
        base = float(timestamps[0])
        t_feats = [
            min(max(float(t) - base, 0.0) / TIME_NORM_SECONDS, 1.0) for t in timestamps
        ]
    else:
        t_feats = [(i / (n_real - 1)) if n_real > 1 else 1.0 for i in range(n_real)]

    rows: List[List[float]] = []
    for _ in range(n_pad):
        rows.append([_PAD_LOG_COEFF, 0.0, 0.0, MATCH_TIME_UNKNOWN, 0.0, 0.0])
    for p, t_norm in zip(pairs, t_feats):
        coeff = float(p[0])
        raw_score = float(p[1]) if len(p) > 1 else 0.0
        mt = p[3] if len(p) > 3 else None
        pts = p[4] if len(p) > 4 else None
        match_time = (
            min(max(float(mt), 0.0) / MATCH_TIME_NORM_SECONDS, 1.0)
            if mt is not None else MATCH_TIME_UNKNOWN
        )
        rows.append([
            math.log(max(coeff, 1.01)),
            scale_match_score(raw_score, sport_path),
            float(t_norm),
            match_time,
            scale_set_points(pts, sport_path),
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
        float(view.get("sport_idx") or 0),
        float(view.get("team1_idx") or 0),
        float(view.get("team2_idx") or 0),
        float(overround) if overround else OVERROUND_UNKNOWN,
        float(view.get("total_line") or 0.0),
        float(int(view.get("period_index") or 0)),
        float(view.get("set_point_diff") or 0.0),
    ]
