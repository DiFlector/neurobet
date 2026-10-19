"""Saved training rows.

Training reads this file. A refresh job builds it once, then appends new matches
in batches of at least ten. Nine new matches wait. Fifteen are written together.
A second refresh waits until the first one has released the lock.
"""

import json
import logging
import os
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import numpy as np

from .dataset import MatchSnapshots

logger = logging.getLogger("neural.dataset_cache")

MIN_NEW_MATCHES = 10


def cache_dir() -> Path:
    root = os.getenv("NEUROBET_DATASET_CACHE", "/app/models/checkpoints/dataset_cache")
    return Path(root)


def ids_to_build(
    candidate_ids: Sequence[str],
    saved_ids: Sequence[str],
    skipped_ids: Sequence[str],
    store_exists: bool,
    minimum: int = MIN_NEW_MATCHES,
) -> List[str]:
    """All unknown matches when the tape is empty or at least `minimum` are waiting."""
    known = set(saved_ids) | set(skipped_ids)
    pending = [str(item) for item in candidate_ids if str(item) not in known]
    if store_exists and len(pending) < minimum:
        return []
    return pending


@contextmanager
def refresh_lock() -> Iterator[bool]:
    """One builder at a time. The caller waits while another refresh holds the lock."""
    import fcntl

    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handle = open(directory / "refresh.lock", "a+")
    fcntl.flock(handle, fcntl.LOCK_EX)
    try:
        yield True
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def read_manifest(sport_code: str, feature_version: str) -> Optional[dict]:
    path = _manifest_path(sport_code, feature_version)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_event_matches(
    sport_code: str,
    feature_version: str,
    event_ids: Sequence[str],
) -> Optional[Tuple[List[MatchSnapshots], List[str]]]:
    """Rows for the requested matches only. The rest of the tape stays on disk."""
    path = _rows_path(sport_code, feature_version)
    if read_manifest(sport_code, feature_version) is None or not path.is_file():
        return None
    wanted = {str(item) for item in event_ids}
    with np.load(path, allow_pickle=False) as payload:
        names = [str(item) for item in payload["feature_names"].tolist()]
        feature_names = np.asarray(payload["feature_names"])
        offsets = np.asarray(payload["offsets"], dtype=np.int64)
        event_id = np.asarray(payload["event_id"])
        started_at = np.asarray(payload["started_at"])
        label = np.asarray(payload["label"])
    keep = [index for index, item in enumerate(event_id.tolist()) if str(item) in wanted]
    if not keep:
        return [], names
    ranges = [(int(offsets[index]), int(offsets[index + 1])) for index in keep]
    match_offsets = np.zeros(len(keep) + 1, dtype=np.int64)
    cursor = 0
    for position, (start, stop) in enumerate(ranges):
        cursor += stop - start
        match_offsets[position + 1] = cursor
    with zipfile.ZipFile(path) as archive:
        available = set(archive.namelist())
        sliced = {
            "feature_names": feature_names,
            "event_id": event_id[keep],
            "started_at": started_at[keep],
            "label": label[keep],
            "offsets": match_offsets,
            "rows": _read_row_ranges(archive, "rows.npy", ranges),
            "observed_at": _read_row_ranges(archive, "observed_at.npy", ranges),
            "row_labels": _read_row_ranges(archive, "row_labels.npy", ranges),
            "row_markets": _read_row_ranges(archive, "row_markets.npy", ranges),
        }
        for key in ("row_odds_a", "row_odds_b"):
            if f"{key}.npy" in available:
                sliced[key] = _read_row_ranges(archive, f"{key}.npy", ranges)
        if "result_available_at.npy" in available:
            with archive.open("result_available_at.npy") as handle:
                sliced["result_available_at"] = np.lib.format.read_array(handle, allow_pickle=False)[keep]
    return _matches_from(sliced), names


def read_store(sport_code: str, feature_version: str) -> Optional[Tuple[List[MatchSnapshots], List[str]]]:
    path = _rows_path(sport_code, feature_version)
    manifest = read_manifest(sport_code, feature_version)
    if manifest is None or not path.is_file():
        return None
    with np.load(path, allow_pickle=False) as payload:
        names = [str(item) for item in payload["feature_names"].tolist()]
        matches = _matches_from(payload)
    logger.info("Loaded %d cached %s matches from %s", len(matches), sport_code, path)
    return matches, names


def write_store(
    sport_code: str,
    feature_version: str,
    matches: Sequence[MatchSnapshots],
    feature_names: Sequence[str],
    skipped_ids: Sequence[str],
    saved_ids: Optional[Sequence[str]] = None,
) -> None:
    """Replace the tape. Callers that append pass the previous ids plus the new ones."""
    directory = _sport_dir(sport_code, feature_version)
    staging = cache_dir() / f"{sport_code}_{feature_version}.next"
    if staging.exists():
        _remove_tree(staging)
    staging.mkdir(parents=True)
    arrays = _arrays_from_matches(matches, feature_names)
    np.savez(staging / "rows.npz", **arrays)
    ids = list(saved_ids) if saved_ids is not None else [match.event_id for match in matches]
    manifest = {
        "sport": sport_code,
        "feature_version": feature_version,
        "feature_names": list(feature_names),
        "saved_ids": ids,
        "skipped_ids": list(skipped_ids),
        "match_count": len(ids),
        "row_count": int(arrays["rows"].shape[0]),
    }
    (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    previous = cache_dir() / f"{sport_code}_{feature_version}.prev"
    if previous.exists():
        _remove_tree(previous)
    if directory.exists():
        directory.rename(previous)
    staging.rename(directory)
    if previous.exists():
        _remove_tree(previous)
    logger.info("Saved %d %s matches (%d rows)", len(ids), sport_code, manifest["row_count"])


def append_store(
    sport_code: str,
    feature_version: str,
    matches: Sequence[MatchSnapshots],
    feature_names: Sequence[str],
    skipped_ids: Sequence[str],
) -> None:
    """Add newly built matches to the tape, keeping start order."""
    current = _rows_path(sport_code, feature_version)
    manifest = read_manifest(sport_code, feature_version)
    if manifest is None or not current.is_file():
        write_store(sport_code, feature_version, matches, feature_names, skipped_ids)
        return
    with np.load(current, allow_pickle=False) as payload:
        old = {key: payload[key] for key in payload.files}
    old = _with_service_columns(old)
    added = _arrays_from_matches(matches, feature_names)
    merged = _merge_arrays(old, added)
    saved_ids = _merge_ids(old, added, list(manifest.get("saved_ids") or []))
    skipped = list(dict.fromkeys([*list(manifest.get("skipped_ids") or []), *list(skipped_ids)]))
    directory = _sport_dir(sport_code, feature_version)
    staging = cache_dir() / f"{sport_code}_{feature_version}.next"
    if staging.exists():
        _remove_tree(staging)
    staging.mkdir(parents=True)
    np.savez(staging / "rows.npz", **merged)
    fresh = {
        "sport": sport_code,
        "feature_version": feature_version,
        "feature_names": list(feature_names),
        "saved_ids": saved_ids,
        "skipped_ids": skipped,
        "match_count": len(saved_ids),
        "row_count": int(merged["rows"].shape[0]),
    }
    (staging / "manifest.json").write_text(json.dumps(fresh), encoding="utf-8")
    previous = cache_dir() / f"{sport_code}_{feature_version}.prev"
    if previous.exists():
        _remove_tree(previous)
    directory.rename(previous)
    staging.rename(directory)
    _remove_tree(previous)
    logger.info("Appended %d %s matches, tape now has %d", len(matches), sport_code, len(saved_ids))


def _merge_ids(old: dict, added: dict, saved_ids: Sequence[str]) -> List[str]:
    old_ids = [str(item) for item in old["event_id"].tolist()]
    new_ids = [str(item) for item in added["event_id"].tolist()]
    if saved_ids and len(saved_ids) == len(old_ids):
        old_ids = [str(item) for item in saved_ids]
    order = _match_order(old["started_at"], added["started_at"])
    combined = old_ids + new_ids
    return [combined[index] for index in order]


def _merge_arrays(old: dict, added: dict) -> Dict[str, np.ndarray]:
    order = _match_order(old["started_at"], added["started_at"])
    old_offsets = np.asarray(old["offsets"], dtype=np.int64)
    new_offsets = np.asarray(added["offsets"], dtype=np.int64)
    old_count = len(old_offsets) - 1
    blocks = []
    for index in order:
        if index < old_count:
            blocks.append((old, int(old_offsets[index]), int(old_offsets[index + 1]), index))
        else:
            shifted = index - old_count
            blocks.append((added, int(new_offsets[shifted]), int(new_offsets[shifted + 1]), shifted))
    total = sum(stop - start for _source, start, stop, _index in blocks)
    width = int(old["rows"].shape[1])
    rows = np.empty((total, width), dtype=np.float32)
    observed = np.empty(total, dtype=np.float64)
    row_labels = np.empty(total, dtype=np.int8)
    markets = np.empty(total, dtype="U64")
    odds_a = np.empty(total, dtype=np.float32)
    odds_b = np.empty(total, dtype=np.float32)
    offsets = np.empty(len(order) + 1, dtype=np.int64)
    event_id = []
    started_at = np.empty(len(order), dtype=np.float64)
    label = np.empty(len(order), dtype=np.int8)
    result_available_at = np.empty(len(order), dtype=np.float64)
    cursor = 0
    offsets[0] = 0
    for position, (source, start, stop, index) in enumerate(blocks):
        count = stop - start
        if count:
            rows[cursor : cursor + count] = source["rows"][start:stop]
            observed[cursor : cursor + count] = source["observed_at"][start:stop]
            row_labels[cursor : cursor + count] = source["row_labels"][start:stop]
            markets[cursor : cursor + count] = source["row_markets"][start:stop]
            odds_a[cursor : cursor + count] = source["row_odds_a"][start:stop]
            odds_b[cursor : cursor + count] = source["row_odds_b"][start:stop]
        cursor += count
        offsets[position + 1] = cursor
        event_id.append(str(source["event_id"][index]))
        started_at[position] = source["started_at"][index]
        label[position] = source["label"][index]
        result_available_at[position] = source["result_available_at"][index]
    return {
        "feature_names": np.array(old["feature_names"]),
        "event_id": np.array(event_id),
        "started_at": started_at,
        "label": label,
        "offsets": offsets,
        "rows": rows,
        "observed_at": observed,
        "row_labels": row_labels,
        "row_markets": markets,
        "row_odds_a": odds_a,
        "row_odds_b": odds_b,
        "result_available_at": result_available_at,
    }


def _match_order(old_started: np.ndarray, new_started: np.ndarray) -> List[int]:
    old_count = len(old_started)
    keyed = [(float(value), index) for index, value in enumerate(old_started)]
    keyed.extend((float(value), old_count + index) for index, value in enumerate(new_started))
    keyed.sort(key=lambda item: (item[0], item[1]))
    return [index for _moment, index in keyed]


def _arrays_from_matches(matches: Sequence[MatchSnapshots], feature_names: Sequence[str]) -> Dict[str, np.ndarray]:
    total = sum(len(match.rows) for match in matches)
    width = len(feature_names)
    rows = np.empty((total, width), dtype=np.float32)
    observed = np.empty(total, dtype=np.float64)
    row_labels = np.empty(total, dtype=np.int8)
    markets = np.empty(total, dtype="U64")
    odds_a = np.full(total, np.nan, dtype=np.float32)
    odds_b = np.full(total, np.nan, dtype=np.float32)
    offsets = np.empty(len(matches) + 1, dtype=np.int64)
    result_available_at = np.full(len(matches), np.nan, dtype=np.float64)
    cursor = 0
    offsets[0] = 0
    for index, match in enumerate(matches):
        count = len(match.rows)
        if count:
            rows[cursor : cursor + count] = np.asarray(match.rows, dtype=np.float32)
            observed[cursor : cursor + count] = [item.timestamp() for item in match.observed_at]
            labels = match.row_labels if match.row_labels is not None else [match.label] * count
            row_labels[cursor : cursor + count] = labels
            stored = match.row_markets if match.row_markets is not None else ["match_winner"] * count
            markets[cursor : cursor + count] = stored
            if match.row_odds_a is not None:
                odds_a[cursor : cursor + count] = match.row_odds_a
            if match.row_odds_b is not None:
                odds_b[cursor : cursor + count] = match.row_odds_b
            if match.row_odds_a is None or match.row_odds_b is None:
                from .commit import side_odds_from_row
                for local, row in enumerate(match.rows):
                    price_a, price_b = side_odds_from_row(row, feature_names)
                    if match.row_odds_a is None:
                        odds_a[cursor + local] = price_a
                    if match.row_odds_b is None:
                        odds_b[cursor + local] = price_b
            if match.result_available_at is not None:
                result_available_at[index] = match.result_available_at.timestamp()
        cursor += count
        offsets[index + 1] = cursor
    return {
        "feature_names": np.array(list(feature_names)),
        "event_id": np.array([match.event_id for match in matches]),
        "started_at": np.array([match.started_at.timestamp() for match in matches], dtype=np.float64),
        "label": np.array([match.label for match in matches], dtype=np.int8),
        "offsets": offsets,
        "rows": rows,
        "observed_at": observed,
        "row_labels": row_labels,
        "row_markets": markets,
        "row_odds_a": odds_a,
        "row_odds_b": odds_b,
        "result_available_at": result_available_at,
    }


def _read_row_ranges(archive: zipfile.ZipFile, member: str, ranges: Sequence[Tuple[int, int]]) -> np.ndarray:
    """Copy only the requested rows out of an uncompressed array inside the tape."""
    with archive.open(member) as handle:
        major, minor = np.lib.format.read_magic(handle)
        if (major, minor) == (1, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_1_0(handle)
        else:
            shape, fortran, dtype = np.lib.format.read_array_header_2_0(handle)
        if fortran:
            raise ValueError(f"{member} is not stored in row order")
        data_at = handle.tell()
        width = int(np.prod(shape[1:], dtype=np.int64)) if len(shape) > 1 else 1
        row_bytes = width * dtype.itemsize
        total = sum(stop - start for start, stop in ranges)
        out = np.empty((total, *shape[1:]), dtype=dtype)
        cursor = 0
        for start, stop in ranges:
            count = stop - start
            if count <= 0:
                continue
            handle.seek(data_at + start * row_bytes)
            blob = handle.read(count * row_bytes)
            chunk = np.frombuffer(blob, dtype=dtype)
            if len(shape) > 1:
                chunk = chunk.reshape((count, *shape[1:]))
            out[cursor : cursor + count] = chunk
            cursor += count
        return out


def _matches_from(payload) -> List[MatchSnapshots]:
    width = len(payload["feature_names"])
    matrix = np.asarray(payload["rows"], dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != width:
        raise ValueError("cached feature width does not match the stored names")
    observed = np.asarray(payload["observed_at"], dtype=np.float64)
    row_labels = np.asarray(payload["row_labels"], dtype=np.int8)
    markets = payload["row_markets"]
    odds_a = np.asarray(payload["row_odds_a"], dtype=float) if "row_odds_a" in payload else np.full(len(matrix), np.nan)
    odds_b = np.asarray(payload["row_odds_b"], dtype=float) if "row_odds_b" in payload else np.full(len(matrix), np.nan)
    available = np.asarray(payload["result_available_at"], dtype=float) if "result_available_at" in payload else None
    missing_quotes = ~np.isfinite(odds_a) | ~np.isfinite(odds_b)
    if np.any(missing_quotes):
        from .commit import side_odds_from_row
        for row_index in np.flatnonzero(missing_quotes):
            odds_a[row_index], odds_b[row_index] = side_odds_from_row(matrix[row_index], payload["feature_names"].tolist())
    offsets = np.asarray(payload["offsets"], dtype=np.int64)
    matches = []
    for index, event_id in enumerate(payload["event_id"].tolist()):
        start = int(offsets[index])
        stop = int(offsets[index + 1])
        matches.append(
            MatchSnapshots(
                event_id=str(event_id),
                started_at=datetime.fromtimestamp(float(payload["started_at"][index]), tz=timezone.utc),
                label=int(payload["label"][index]),
                rows=matrix[start:stop],
                observed_at=[datetime.fromtimestamp(float(observed[row]), tz=timezone.utc) for row in range(start, stop)],
                row_labels=[int(row_labels[row]) for row in range(start, stop)],
                row_markets=[str(markets[row]) for row in range(start, stop)],
                row_odds_a=[float(odds_a[row]) for row in range(start, stop)],
                row_odds_b=[float(odds_b[row]) for row in range(start, stop)],
                result_available_at=(
                    datetime.fromtimestamp(float(available[index]), tz=timezone.utc)
                    if available is not None and np.isfinite(available[index]) else None
                ),
            )
        )
    return matches


def _with_service_columns(payload: dict) -> dict:
    """Upgrade older cached arrays in memory before an append."""
    count = len(payload["rows"])
    if "row_odds_a" not in payload or "row_odds_b" not in payload:
        from .commit import side_odds_from_row
        derived = [side_odds_from_row(row, payload["feature_names"].tolist()) for row in payload["rows"]]
        payload.setdefault("row_odds_a", np.asarray([item[0] for item in derived], dtype=np.float32))
        payload.setdefault("row_odds_b", np.asarray([item[1] for item in derived], dtype=np.float32))
    payload.setdefault("result_available_at", np.full(len(payload["event_id"]), np.nan, dtype=np.float64))
    return payload


def _sport_dir(sport_code: str, feature_version: str) -> Path:
    return cache_dir() / f"{sport_code}_{feature_version}"


def _rows_path(sport_code: str, feature_version: str) -> Path:
    return _sport_dir(sport_code, feature_version) / "rows.npz"


def _manifest_path(sport_code: str, feature_version: str) -> Path:
    return _sport_dir(sport_code, feature_version) / "manifest.json"


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_file():
            child.unlink()
    path.rmdir()
