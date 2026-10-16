"""Point-in-time tennis player form.

A player's past result is visible only when that match finished at or before
the cutoff. The match being scored is excluded. An unknown player stays at
one half, not at zero or one.
"""

import bisect
from datetime import datetime, timezone
from typing import Dict, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

PRIOR_WINS = 2.0
PRIOR_MATCHES = 4.0

PLAYER_FEATURE_NAMES = (
    "player_prior_matches_a",
    "player_prior_matches_b",
    "player_win_rate_a",
    "player_win_rate_b",
    "player_win_rate_diff",
    "h2h_matches",
    "h2h_win_rate_a",
)


def canonical_player_name(name: Optional[str]) -> str:
    text = (name or "").strip()
    if not text:
        return ""
    try:
        from tennis_adapter.normalizer import normalize_russian_player_name
    except ImportError:
        return text
    return normalize_russian_player_name(text)


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _rate(wins: float, matches: float) -> float:
    return (wins + PRIOR_WINS) / (matches + PRIOR_MATCHES)


class PlayerHistory:
    """Finished matches sorted by finish time."""

    def __init__(self, rows: Sequence[dict]):
        self.rows = sorted(rows, key=lambda row: _as_utc(row["finished_at"]))
        self._times = [_as_utc(row["finished_at"]) for row in self.rows]
        self._cache: Dict[tuple, Dict[str, float]] = {}

    @classmethod
    def load(cls, session: Session) -> "PlayerHistory":
        from db.models import Event

        from .dataset import _winner_token, match_winner_label

        events = session.execute(
            select(Event)
            .where(Event.sport_code == "tennis")
            .where(Event.status.in_(["finished", "completed"]))
            .where(Event.finished_at.is_not(None))
        ).scalars()
        rows = []
        for event in events:
            label = match_winner_label(
                _winner_token(event),
                event.participant_a_name,
                event.participant_b_name,
                event.current_score or {},
                "tennis",
            )
            if label is None or event.finished_at is None:
                continue
            rows.append(
                {
                    "event_id": str(event.id),
                    "finished_at": event.finished_at,
                    "name_a": canonical_player_name(event.participant_a_name),
                    "name_b": canonical_player_name(event.participant_b_name),
                    "side_a_won": label == 1,
                }
            )
        return cls(rows)

    def features(
        self,
        name_a: Optional[str],
        name_b: Optional[str],
        cutoff: datetime,
        exclude_event_id: Optional[str] = None,
    ) -> Dict[str, float]:
        left = canonical_player_name(name_a)
        right = canonical_player_name(name_b)
        limit = _as_utc(cutoff)
        end = bisect.bisect_right(self._times, limit)
        key = (left, right, str(exclude_event_id or ""), end)
        cached = self._cache.get(key)
        if cached is not None:
            return dict(cached)
        wins_a = matches_a = wins_b = matches_b = 0.0
        h2h = h2h_wins_a = 0.0
        excluded = str(exclude_event_id) if exclude_event_id else ""
        for row in self.rows[:end]:
            if excluded and row["event_id"] == excluded:
                continue
            wins_a, matches_a = _count_side(row, left, wins_a, matches_a)
            wins_b, matches_b = _count_side(row, right, wins_b, matches_b)
            if left and right and left != right and {row["name_a"], row["name_b"]} == {left, right}:
                h2h += 1.0
                if row["name_a"] == left:
                    h2h_wins_a += 1.0 if row["side_a_won"] else 0.0
                else:
                    h2h_wins_a += 0.0 if row["side_a_won"] else 1.0
        rate_a = _rate(wins_a, matches_a)
        rate_b = _rate(wins_b, matches_b)
        result = {
            "player_prior_matches_a": matches_a,
            "player_prior_matches_b": matches_b,
            "player_win_rate_a": rate_a,
            "player_win_rate_b": rate_b,
            "player_win_rate_diff": rate_a - rate_b,
            "h2h_matches": h2h,
            "h2h_win_rate_a": _rate(h2h_wins_a, h2h),
        }
        self._cache[key] = result
        return dict(result)


def _count_side(row: dict, name: str, wins: float, matches: float) -> tuple:
    if not name:
        return wins, matches
    if row["name_a"] == name:
        return wins + (1.0 if row["side_a_won"] else 0.0), matches + 1.0
    if row["name_b"] == name:
        return wins + (0.0 if row["side_a_won"] else 1.0), matches + 1.0
    return wins, matches
