"""Small durable hourly counters for tennis source routing and HTTP activity."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any

from sqlalchemy import text

logger = logging.getLogger("research.source_metrics")


def summarize_source_metrics(payload: dict[str, Any] | None, *, now: datetime | None = None) -> dict[str, Any]:
    """Return bounded rolling-24-hour counters from persisted hourly buckets."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    buckets = (payload or {}).get("buckets") or {}
    totals = {
        "requests": 0, "successful_requests": 0, "errors": 0,
        "published": 0, "prevented_flashscore": 0,
        "latency_total_ms": 0, "latency_samples": 0,
    }
    max_latency_ms = 0
    for key, bucket in buckets.items():
        try:
            bucket_at = datetime.fromisoformat(str(key).replace("Z", "+00:00"))
            if bucket_at.tzinfo is None:
                bucket_at = bucket_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if bucket_at < cutoff or bucket_at > now:
            continue
        for field in totals:
            value = bucket.get(field, 0)
            if isinstance(value, (int, float)):
                totals[field] += int(value)
        max_latency_ms = max(max_latency_ms, int(bucket.get("latency_max_ms", 0) or 0))
    latency_samples = totals.pop("latency_samples")
    latency_total = totals.pop("latency_total_ms")
    totals["avg_latency_ms"] = round(latency_total / latency_samples) if latency_samples else None
    totals["max_latency_ms"] = max_latency_ms
    totals["window"] = "rolling_24h"
    return totals


def record_source_metric(
    engine,
    source: str,
    *,
    request: bool = False,
    successful: bool | None = None,
    published: int = 0,
    duration_ms: int | None = None,
    status_code: int | None = None,
    error_code: str | None = None,
    prevented_flashscore: int = 0,
    now: datetime | None = None,
) -> None:
    """Atomically increment one source's current UTC-hour metric bucket."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    bucket_key = now.replace(minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
            VALUES (:source,'request_metrics','{}'::jsonb,now())
            ON CONFLICT(source,state_key) DO NOTHING
        """), {"source": source})
        row = conn.execute(text("""
            SELECT state_json FROM external_tennis_source_state
            WHERE source=:source AND state_key='request_metrics' FOR UPDATE
        """), {"source": source}).scalar()
        state = row if isinstance(row, dict) else {}
        buckets = dict(state.get("buckets") or {})
        bucket = dict(buckets.get(bucket_key) or {})
        for field in ("requests", "successful_requests", "errors", "published",
                      "prevented_flashscore", "latency_total_ms", "latency_samples", "latency_max_ms"):
            bucket[field] = int(bucket.get(field, 0) or 0)
        if request:
            bucket["requests"] += 1
            if successful:
                bucket["successful_requests"] += 1
            else:
                bucket["errors"] += 1
            if duration_ms is not None and duration_ms >= 0:
                bucket["latency_total_ms"] += int(duration_ms)
                bucket["latency_samples"] += 1
                bucket["latency_max_ms"] = max(bucket["latency_max_ms"], int(duration_ms))
            if status_code is not None:
                bucket["last_status_code"] = int(status_code)
            if error_code:
                bucket["last_error_code"] = str(error_code)[:96]
        bucket["published"] += max(0, int(published))
        bucket["prevented_flashscore"] += max(0, int(prevented_flashscore))
        bucket["updated_at"] = now.isoformat()
        buckets[bucket_key] = bucket
        keep_after = now - timedelta(hours=48)
        for key in list(buckets):
            try:
                bucket_at = datetime.fromisoformat(str(key).replace("Z", "+00:00"))
            except ValueError:
                del buckets[key]
                continue
            if bucket_at.tzinfo is None:
                bucket_at = bucket_at.replace(tzinfo=timezone.utc)
            if bucket_at < keep_after:
                del buckets[key]
        conn.execute(text("""
            UPDATE external_tennis_source_state
            SET state_json=CAST(:payload AS jsonb),updated_at=now()
            WHERE source=:source AND state_key='request_metrics'
        """), {"source": source, "payload": json.dumps({"buckets": buckets})})


def try_record_source_metric(engine, source: str, **kwargs) -> None:
    """Metrics are best-effort and must never fail a source fetch or publication."""
    try:
        record_source_metric(engine, source, **kwargs)
    except Exception:
        logger.exception("Could not persist source metrics source=%s", source)


def try_record_prevented_flashscore(engine, source: str | None) -> bool:
    """Count one verified alternative-source cache hit that skipped Flashscore."""
    if not source:
        return False
    try_record_source_metric(engine, source, prevented_flashscore=1)
    return True
