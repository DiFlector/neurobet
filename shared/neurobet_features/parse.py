"""Parsers shared by archive (backend) and live/train/backtest (ai_service).

Clock-shaped timers stay seconds. Racket-sport strings like
`(12-10 5-11 7-9*)` are not a clock — they carry set scores; we recover
elapsed-time proxy + current-set point diff instead of returning unknown.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, NamedTuple, Optional

_TIMER_MMSS_RE = re.compile(r"^(\d{1,3}):([0-5]\d)$")
_TIMER_PLUS_RE = re.compile(r"^(\d{1,3})\+(\d{1,2})'?$")
_TIMER_MIN_RE = re.compile(r"^(\d{1,3})'$")
# "12-10", "7:9", "7-9*", "11*-10" — pairs inside a free-text timer.
_SCORE_PAIR_RE = re.compile(r"(\d+)\s*\*?\s*[-:]\s*(\d+)\s*\*?")
# "1-й сет" / "2-й период" / "3-я четверть". Trailing unit required so
# "1-я карта" does not parse as a period (see backend resolve_outcome).
_PERIOD_ORDINAL_RE = re.compile(
    r"^(\d+)-[а-яё]+\s+(сет|тайм|период|четверть|половина)\b",
    re.IGNORECASE,
)

# Synthetic elapsed-time for a racket string: ~8 min per completed set,
# ~20 s per point in the current one. Sport embedding calibrates the scale.
_RACKET_SET_SECONDS = 480.0
_RACKET_POINT_SECONDS = 20.0


class TimerParse(NamedTuple):
    match_time_seconds: Optional[float]
    set_point_diff: Optional[float]


def parse_ts_epoch(raw: Any) -> Optional[float]:
    """odds_history.timestamp ("YYYY-MM-DD HH:MM:SS", naive Moscow) -> epoch.
    Only consumed as differences between snapshots of one bet."""
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(str(raw)).timestamp()
    except Exception:
        return None


def parse_score_pair(score_at_time: Any) -> tuple[int, int]:
    """Split 'a:b' match/set score into integer parts."""
    try:
        a, b = str(score_at_time or "0:0").split(":", 1)
        return int(a), int(b)
    except Exception:
        return 0, 0


def parse_score_diff(score_at_time: Any) -> int:
    a, b = parse_score_pair(score_at_time)
    return a - b


def parse_score_sum(score_at_time: Any) -> int:
    a, b = parse_score_pair(score_at_time)
    return a + b


def parse_period_ordinal(prefix: str) -> Optional[int]:
    """'1-й тайм' -> 1, '2-й сет' -> 2. Main-match / unknown -> None."""
    m = _PERIOD_ORDINAL_RE.match((prefix or "").strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def period_index(prefix: str) -> int:
    """0 = основной матч / неизвестный префикс, 1+ = порядковый период."""
    ordinal = parse_period_ordinal(prefix)
    return int(ordinal) if ordinal and ordinal > 0 else 0


def parse_timer(raw: Any) -> TimerParse:
    s = str(raw or "").strip()
    if not s:
        return TimerParse(None, None)

    clock = _parse_clock_seconds(s)
    pairs = [(int(a), int(b)) for a, b in _SCORE_PAIR_RE.findall(s)]
    # A lone MM:SS clock is also one regex pair (45:23 -> (45, 23)). Only
    # treat as set scores when the string is clearly not a clock, or when
    # there are two+ pairs (multi-set scoreline).
    if clock is not None and len(pairs) <= 1:
        return TimerParse(clock, None)
    if len(pairs) >= 1 and clock is None:
        completed = max(len(pairs) - 1, 0)
        p1, p2 = pairs[-1]
        elapsed = completed * _RACKET_SET_SECONDS + (p1 + p2) * _RACKET_POINT_SECONDS
        return TimerParse(elapsed, float(p1 - p2))
    if clock is not None:
        return TimerParse(clock, None)
    return TimerParse(None, None)


def parse_timer_seconds(raw: Any) -> Optional[float]:
    """Clock seconds or racket elapsed proxy — what timer_seq stored as a float."""
    return parse_timer(raw).match_time_seconds


def pack_timer_entry(
    match_time_seconds: Optional[float],
    set_point_diff: Optional[float] = None,
) -> Any:
    """JSON value for timer_seq_json. Old rows are a bare float; new rows with
    a set-point signal are [seconds, point_diff] so loaders can unpack both."""
    if match_time_seconds is None and set_point_diff is None:
        return None
    if set_point_diff is None:
        return match_time_seconds
    return [match_time_seconds, set_point_diff]


def unpack_timer_entry(value: Any) -> TimerParse:
    if value is None:
        return TimerParse(None, None)
    if isinstance(value, (int, float)):
        return TimerParse(float(value), None)
    if isinstance(value, (list, tuple)):
        seconds = value[0] if len(value) > 0 else None
        points = value[1] if len(value) > 1 else None
        return TimerParse(
            None if seconds is None else float(seconds),
            None if points is None else float(points),
        )
    return TimerParse(None, None)


def _parse_clock_seconds(s: str) -> Optional[float]:
    m = _TIMER_MMSS_RE.match(s)
    if m:
        return float(int(m.group(1)) * 60 + int(m.group(2)))
    m = _TIMER_PLUS_RE.match(s)
    if m:
        return float((int(m.group(1)) + int(m.group(2))) * 60)
    m = _TIMER_MIN_RE.match(s)
    if m:
        return float(int(m.group(1)) * 60)
    return None
