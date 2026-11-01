"""Recover only exact, hash-verified Fonbet raw witnesses for saved tennis snapshots."""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from datetime import time as day_time
import sys
import uuid

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

_APP_ROOT = Path("/app")
if (_APP_ROOT / "app" / "tennis_pit_raw_evidence.py").is_file():
    sys.path.insert(0, str(_APP_ROOT))

from db.connection import SessionLocal
from db.models import TennisEventStateRawEvidence
from app.tennis_pit_raw_evidence import verify_fonbet_snapshot_evidence


def _utc(value):
    value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("snapshot_timestamp_without_timezone")
    return value.astimezone(timezone.utc)


def _read_index(path: str) -> tuple[list[dict], str]:
    raw = Path(path).read_bytes()
    candidates = {}
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        event_id = uuid.UUID(row["fonbet_event_id"])
        for source in (row, row.get("result_time_snapshot_evidence") or {}):
            if not source.get("snapshot_id"):
                continue
            timestamp = source.get("snapshot_observed_at") or source.get("observed_at")
            if not timestamp:
                continue
            observed_at = _utc(timestamp)
            key = (uuid.UUID(source["snapshot_id"]), observed_at)
            prior = candidates.get(key)
            if prior is not None and prior != event_id:
                raise ValueError(f"snapshot_index_event_conflict:line_{number}")
            candidates[key] = event_id
    return ([{"snapshot_id": snapshot_id, "observed_at": observed_at, "event_id": event_id}
             for (snapshot_id, observed_at), event_id in sorted(candidates.items(), key=lambda item: item[0][1])],
            hashlib.sha256(raw).hexdigest())


def _load_snapshots(items: list[dict]) -> list[dict]:
    rows = []
    by_day = defaultdict(list)
    for item in items:
        by_day[item["observed_at"].date().isoformat()].append(item)
    with SessionLocal() as session:
        for _day, batch in sorted(by_day.items()):
            found = session.execute(text("""
                WITH requested AS (
                  SELECT * FROM unnest(CAST(:ids AS uuid[]), CAST(:times AS timestamptz[]), CAST(:events AS uuid[]))
                  AS r(id, observed_at, event_id)
                )
                SELECT s.id,s.observed_at,s.event_id,s.source_event_id,s.score,s.score_detail,
                       s.raw_hash,s.raw_object_key
                FROM requested r JOIN LATERAL (
                  SELECT id,observed_at,event_id,source_event_id,score,score_detail,raw_hash,raw_object_key
                  FROM event_state_snapshots
                  WHERE id=r.id AND observed_at=r.observed_at AND event_id=r.event_id
                    AND sport_code='tennis' AND observed_at >= :lower AND observed_at <= :upper
                  LIMIT 1
                ) s ON TRUE
            """), {"ids": [item["snapshot_id"] for item in batch],
                    "times": [item["observed_at"] for item in batch],
                    "events": [item["event_id"] for item in batch],
                    "lower": min(item["observed_at"] for item in batch),
                    "upper": max(item["observed_at"] for item in batch)}).mappings().all()
            rows.extend(dict(row) for row in found)
    return rows


def _s3():
    from app.storage import ResearchStorage

    storage = ResearchStorage()
    client = storage._get_client()
    if client is None:
        raise RuntimeError("minio_client_unavailable")
    return client, os.getenv("MINIO_BUCKET_RAW", "raw-snapshots")


def _list_day(client, bucket: str, day: str) -> list[dict]:
    objects = []
    token = None
    prefix = f"fonbet/tennis/{day}/"
    while True:
        args = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            args["ContinuationToken"] = token
        page = client.list_objects_v2(**args)
        objects.extend({"key": row["Key"], "last_modified": row["LastModified"].astimezone(timezone.utc)}
                       for row in page.get("Contents", []))
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
    objects.sort(key=lambda row: (row["last_modified"], row["key"]))
    return objects


def backfill(snapshot_index: str, *, commit: bool = False, max_age_seconds: int = 30,
             limit: int | None = None) -> dict:
    if max_age_seconds < 0 or max_age_seconds > 600:
        raise ValueError("max_age_seconds_must_be_0_to_600")
    indexed, index_sha256 = _read_index(snapshot_index)
    if limit:
        indexed = indexed[:limit]
    rows = _load_snapshots(indexed)
    by_day = defaultdict(list)
    for row in rows:
        row["observed_at"] = _utc(row["observed_at"])
        if row.get("raw_hash") and row.get("raw_object_key"):
            continue
        by_day[row["observed_at"].date().isoformat()].append(row)

    client, bucket = _s3()
    report = {
        "mode": "commit" if commit else "dry_run", "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_index": snapshot_index, "snapshot_index_sha256": index_sha256,
        "indexed_snapshots": len(indexed), "loaded_snapshots": len(rows),
        "missing_snapshots": len(indexed) - len(rows), "max_candidate_age_seconds": max_age_seconds,
        "s3_last_modified_role": "candidate_search_only_never_used_as_available_at",
        "verified": 0, "rejected": 0, "inserted": 0, "reasons": {}, "examples": [],
    }
    reason_counts = Counter()
    verified_rows = []
    body_cache = {}
    raw_objects_read = 0
    from tennis_adapter.adapter import TennisSportAdapter
    adapter = TennisSportAdapter()

    for day, day_rows in sorted(by_day.items()):
        objects = _list_day(client, bucket, day)
        day_start = datetime.combine(datetime.fromisoformat(day).date(), day_time.min, tzinfo=timezone.utc)
        if min(row["observed_at"] for row in day_rows) - timedelta(seconds=max_age_seconds) < day_start:
            previous_day = (datetime.fromisoformat(day).date() - timedelta(days=1)).isoformat()
            objects.extend(_list_day(client, bucket, previous_day))
            objects.sort(key=lambda item: (item["last_modified"], item["key"]))
        lm_values = [item["last_modified"] for item in objects]
        for row in day_rows:
            snapshot_at = row["observed_at"]
            end = bisect_right(lm_values, snapshot_at)
            reason = "no_pre_cutoff_raw_candidate"
            verified = None
            lower = snapshot_at - timedelta(seconds=max_age_seconds)
            for object_index in range(end - 1, -1, -1):
                candidate = objects[object_index]
                if candidate["last_modified"] < lower:
                    break
                cache_key = candidate["key"]
                if cache_key not in body_cache:
                    try:
                        body_cache[cache_key] = client.get_object(Bucket=bucket, Key=cache_key)["Body"].read()
                    except Exception:
                        body_cache[cache_key] = None
                    raw_objects_read += 1
                body = body_cache[cache_key]
                if body is None:
                    reason = "raw_object_read_failed"
                    continue
                checked = verify_fonbet_snapshot_evidence(
                    raw_key=cache_key, body=body, source_event_id=row["source_event_id"],
                    snapshot_at=snapshot_at, expected_score=row.get("score"),
                    expected_state=row.get("score_detail") or {},
                    state_parser=lambda raw, _adapter=adapter: _adapter.parse_state(raw).model_dump(mode="json"),
                )
                if checked.get("verified"):
                    verified = {"row": row, "raw_key": cache_key, "checked": checked,
                                "candidate_last_modified": candidate["last_modified"]}
                    break
                reason = checked.get("reason") or "unverified"
            if verified:
                verified_rows.append(verified)
                report["verified"] += 1
            else:
                report["rejected"] += 1
                reason_counts[reason] += 1
            if len(report["examples"]) < 20:
                report["examples"].append({
                    "snapshot_id": str(row["id"]), "snapshot_observed_at": snapshot_at.isoformat(),
                    "source_event_id": row["source_event_id"], "verified": bool(verified),
                    "reason": None if verified else reason,
                    "raw_object_key": verified["raw_key"] if verified else None,
                    "available_at": verified["checked"]["available_at"].isoformat() if verified else None,
                })
        body_cache.clear()

    if commit and verified_rows:
        with SessionLocal() as session:
            for start in range(0, len(verified_rows), 250):
                batch = verified_rows[start:start + 250]
                values = []
                for item in batch:
                    row, checked = item["row"], item["checked"]
                    values.append({
                        "id": uuid.uuid4(), "snapshot_id": row["id"], "snapshot_observed_at": row["observed_at"],
                        "event_id": row["event_id"], "source_event_id": row["source_event_id"],
                        "available_at": checked["available_at"], "raw_object_key": item["raw_key"],
                        "raw_content_hash": checked["raw_content_hash"], "parser_version": checked["parser_version"],
                        "evidence_hash": checked["evidence_hash"],
                        "evidence_json": {**checked["evidence_json"],
                                       "candidate_last_modified": item["candidate_last_modified"].isoformat(),
                                       "last_modified_was_not_used_for_availability": True},
                    })
                statement = pg_insert(TennisEventStateRawEvidence).values(values).on_conflict_do_nothing(
                    constraint="uq_tennis_event_state_raw_evidence"
                )
                result = session.execute(statement)
                session.commit()
                report["inserted"] += result.rowcount or 0
    report["reasons"] = dict(reason_counts)
    report["raw_objects_read"] = raw_objects_read
    report["reconstruction_parser_version"] = "tennis-fonbet-state-reparse-v1"
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-index", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--commit", action="store_true", help="Insert verified immutable links; default is dry-run")
    parser.add_argument("--max-age-seconds", type=int, default=30)
    parser.add_argument("--limit", type=int, help="Bound the number of distinct indexed snapshots")
    args = parser.parse_args()
    result = backfill(args.snapshot_index, commit=args.commit, max_age_seconds=args.max_age_seconds, limit=args.limit)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "mode", "indexed_snapshots", "loaded_snapshots", "missing_snapshots", "verified",
        "rejected", "inserted", "reasons", "raw_objects_read",
    )}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
