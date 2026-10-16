"""Train, validation, and a frozen test, split by match time."""

import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Sequence, Tuple

from .rows import CompareRow


def split_fixed_test(
    rows: Sequence[CompareRow],
    test_matches: int = 100,
    validation_fraction: float = 0.25,
    embargo: timedelta = timedelta(hours=6),
) -> Tuple[List[CompareRow], List[CompareRow], List[CompareRow]]:
    """Keep the latest matches as one frozen test. Tuning sees only earlier matches.

    The test size stays exact. A match that crosses the cut is removed from the
    earlier part, so a long match cannot be learned and then scored.
    """
    if test_matches < 1:
        raise ValueError("Размер замороженного теста должен быть положительным.")
    grouped: Dict[str, List[CompareRow]] = {}
    for row in rows:
        grouped.setdefault(row.event_id, []).append(row)
    matches = sorted(grouped.values(), key=lambda items: (items[0].started_at, items[0].event_id))
    if len(matches) < test_matches + 8:
        raise ValueError(
            f"Нужно минимум {test_matches + 8} матчей для holdout из {test_matches}, сейчас {len(matches)}."
        )
    test_block = matches[-test_matches:]
    earlier = _drop_crossing(matches[:-test_matches], test_block[0][0].started_at, embargo)
    if len(earlier) < 8:
        earlier = _drop_crossing(matches[:-test_matches], test_block[0][0].started_at, timedelta(0))
    if len(earlier) < 8:
        raise ValueError("После embargo не осталось ранних матчей для обучения и валидации.")
    validation_count = max(1, int(round(len(earlier) * validation_fraction)))
    validation_count = min(validation_count, len(earlier) - 1)
    validation_block = earlier[-validation_count:]
    train_block = _drop_crossing(earlier[:-validation_count], validation_block[0][0].started_at, embargo)
    if not train_block:
        train_block = _drop_crossing(earlier[:-validation_count], validation_block[0][0].started_at, timedelta(0))
    if not train_block:
        raise ValueError("После embargo обучающая часть пустая.")
    return _flatten(train_block), _flatten(validation_block), _flatten(test_block)


def _flatten(matches: Sequence[Sequence[CompareRow]]) -> List[CompareRow]:
    rows: List[CompareRow] = []
    for items in matches:
        rows.extend(items)
    return rows


def _drop_crossing(matches: Sequence[Sequence[CompareRow]], cut: datetime, gap: timedelta):
    kept = []
    for items in matches:
        start = items[0].started_at
        finish = max(item.finished_at for item in items)
        if start < cut + gap and finish > cut - gap:
            continue
        kept.append(items)
    return kept


def split_matches(
    rows: Sequence[CompareRow],
    train_fraction: float = 0.6,
    validation_fraction: float = 0.2,
    embargo: timedelta = timedelta(hours=6),
) -> Tuple[List[CompareRow], List[CompareRow], List[CompareRow]]:
    """Earlier matches train, the next block validates, the last block is the test.

    A match whose interval crosses a boundary is dropped. All of its rows stay together.
    """
    grouped: Dict[str, List[CompareRow]] = {}
    for row in rows:
        grouped.setdefault(row.event_id, []).append(row)
    matches = sorted(grouped.values(), key=lambda items: items[0].started_at)
    count = len(matches)
    if count < 6:
        raise ValueError(f"Нужно минимум 6 матчей для train/val/test, сейчас {count}.")
    first_time = matches[0][0].started_at
    last_time = matches[-1][0].started_at
    span = last_time - first_time
    if span.total_seconds() <= 0:
        span = timedelta(seconds=count)
        first_time = matches[0][0].started_at
    first_cut = first_time + span * train_fraction
    second_cut = first_time + span * (train_fraction + validation_fraction)
    if second_cut <= first_cut:
        second_cut = first_cut + timedelta(seconds=1)

    def crosses(items: Sequence[CompareRow], boundary: datetime, gap: timedelta) -> bool:
        start = items[0].started_at
        finish = max(item.finished_at for item in items)
        return start < boundary + gap and finish > boundary - gap

    def assign(gap: timedelta):
        train, validation, test = [], [], []
        for items in matches:
            if crosses(items, first_cut, gap) or crosses(items, second_cut, gap):
                continue
            started = items[0].started_at
            if started < first_cut:
                train.extend(items)
            elif started < second_cut:
                validation.extend(items)
            else:
                test.extend(items)
        return train, validation, test

    # A wide embargo on a few dense days can erase a whole part. Fall back to
    # dropping only the matches that actually touch the boundary. If the clock
    # cut still leaves a part too small, keep the same order and cut by match count.
    for gap in (embargo, timedelta(0)):
        train, validation, test = assign(gap)
        if train and validation and test and _part_is_usable(train, validation, test, count):
            return train, validation, test
    return _split_by_order(matches)


def _match_count(rows: Sequence[CompareRow]) -> int:
    return len({row.event_id for row in rows})


def _part_is_usable(train, validation, test, total: int) -> bool:
    smallest = min(_match_count(train), _match_count(validation), _match_count(test))
    return smallest >= 40 or total < 120


def _split_by_order(matches: Sequence[Sequence[CompareRow]]):
    count = len(matches)
    train_end = max(1, int(count * 0.6))
    validation_end = max(train_end + 1, int(count * 0.8))
    if validation_end >= count:
        validation_end = count - 1
    train, validation, test = [], [], []
    for index, items in enumerate(matches):
        if index < train_end:
            train.extend(items)
        elif index < validation_end:
            validation.extend(items)
        else:
            test.extend(items)
    return train, validation, test


def event_ids(rows: Sequence[CompareRow]) -> List[str]:
    seen = []
    known = set()
    for row in rows:
        if row.event_id not in known:
            known.add(row.event_id)
            seen.append(row.event_id)
    return seen


def test_fingerprint(rows: Sequence[CompareRow]) -> str:
    payload = "\n".join(sorted(event_ids(rows)))
    return hashlib.sha256(payload.encode()).hexdigest()


def assert_disjoint(train: Sequence[CompareRow], validation: Sequence[CompareRow], test: Sequence[CompareRow]) -> None:
    parts = [set(event_ids(train)), set(event_ids(validation)), set(event_ids(test))]
    if parts[0] & parts[1] or parts[0] & parts[2] or parts[1] & parts[2]:
        raise RuntimeError("Один матч попал в две части разбиения.")
