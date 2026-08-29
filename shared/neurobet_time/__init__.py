"""NeuroBet wall-clock time — always Europe/Moscow (UTC+3, no DST)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

MOSCOW_TZ = timezone(timedelta(hours=3))


def now_moscow() -> datetime:
    return datetime.now(MOSCOW_TZ)


def now_moscow_naive() -> datetime:
    """Moscow wall-clock without tzinfo — matches TEXT columns written as local Moscow."""
    return now_moscow().replace(tzinfo=None)


def now_moscow_iso() -> str:
    """ISO 8601 with +03:00 offset."""
    return now_moscow().replace(microsecond=0).isoformat()


def now_moscow_stamp() -> str:
    return now_moscow().strftime("%Y%m%d-%H%M%S")


def parse_iso_datetime(raw: Any) -> Optional[datetime]:
    """Parse ISO timestamps; naive strings are treated as Moscow."""
    if raw is None:
        return None
    try:
        text = str(raw).strip()
        if not text:
            return None
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MOSCOW_TZ)
        return dt
    except Exception:
        return None
