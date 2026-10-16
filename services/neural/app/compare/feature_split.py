"""Chronological holdout for the production tennis feature tape.

The latest block already reported by the 13-feature comparison stays out of
this experiment. The new test is the 100 matches immediately before it.
"""

import hashlib
from datetime import timedelta
from typing import List, Sequence, Tuple

from ..dataset import MatchSnapshots


def split_unseen_holdout(
    matches: Sequence[MatchSnapshots],
    reserved_ids: Sequence[str],
    test_matches: int = 100,
    validation_fraction: float = 0.25,
    embargo: timedelta = timedelta(hours=6),
) -> Tuple[List[MatchSnapshots], List[MatchSnapshots], List[MatchSnapshots]]:
    """Train and validation stay before the frozen test. Later matches are dropped."""
    if test_matches < 1:
        raise ValueError("Размер замороженного теста должен быть положительным.")
    ordered = sorted(matches, key=lambda match: (match.started_at, str(match.event_id)))
    reserved = {str(event_id) for event_id in reserved_ids}
    blocked_at = [
        match.started_at for match in ordered if str(match.event_id) in reserved
    ]
    if blocked_at:
        first_blocked = min(blocked_at)
        usable = [
            match
            for match in ordered
            if match.started_at < first_blocked and str(match.event_id) not in reserved
        ]
    else:
        if len(ordered) <= test_matches:
            raise ValueError("Нет более раннего блока, который можно отделить от последних матчей.")
        usable = list(ordered[:-test_matches])
    if len(usable) < test_matches + 8:
        raise ValueError(
            f"Нужно минимум {test_matches + 8} матчей до зарезервированного теста, сейчас {len(usable)}."
        )
    test = usable[-test_matches:]
    earlier = _before(usable[:-test_matches], test[0].started_at, embargo)
    if len(earlier) < 8:
        earlier = _before(usable[:-test_matches], test[0].started_at, timedelta(0))
    if len(earlier) < 8:
        raise ValueError("После embargo не осталось ранних матчей.")
    validation_count = max(1, int(round(len(earlier) * validation_fraction)))
    validation_count = min(validation_count, len(earlier) - 1)
    validation = earlier[-validation_count:]
    train = _before(earlier[:-validation_count], validation[0].started_at, embargo)
    if not train:
        train = _before(earlier[:-validation_count], validation[0].started_at, timedelta(0))
    if not train:
        raise ValueError("После embargo обучающая часть пустая.")
    parts = (train, validation, test)
    _assert_disjoint(parts)
    leaked = reserved & {str(match.event_id) for part in parts for match in part}
    if leaked:
        raise RuntimeError("Зарезервированный тест попал в новое разбиение.")
    return train, validation, test


def _before(matches: Sequence[MatchSnapshots], cut, gap: timedelta) -> List[MatchSnapshots]:
    return [match for match in matches if match.started_at <= cut - gap]


def _assert_disjoint(parts: Sequence[Sequence[MatchSnapshots]]) -> None:
    seen = set()
    for part in parts:
        ids = {str(match.event_id) for match in part}
        if seen & ids:
            raise RuntimeError("Один матч попал в две части разбиения.")
        seen |= ids


def event_ids(matches: Sequence[MatchSnapshots]) -> List[str]:
    return [str(match.event_id) for match in matches]


def fingerprint(matches: Sequence[MatchSnapshots]) -> str:
    payload = "\n".join(sorted(event_ids(matches)))
    return hashlib.sha256(payload.encode()).hexdigest()
