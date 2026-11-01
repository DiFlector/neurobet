"""Periodic summary of per-snapshot pre-label PIT evidence written by Neural."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

logger = logging.getLogger("research.tennis_pit_coverage")


def _engine():
    from db.connection import get_database_url

    return create_engine(get_database_url(), poolclass=NullPool, pool_pre_ping=True)


def build_recent_tennis_pit_coverage(*, lookback_hours: int = 48) -> dict:
    """Aggregate the immutable six-group readiness checks for recent forecasts."""
    if not 1 <= int(lookback_hours) <= 168:
        raise ValueError("lookback_hours_must_be_1_to_168")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=int(lookback_hours))
    engine = _engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT f.id, f.event_id, f.observed_at, e.scheduled_start_at,
                       f.forecast_json->'data_quality_detail'->'prelabel_pit_readiness' AS readiness
                FROM tennis_forecast_snapshots f
                JOIN events e ON e.id=f.event_id
                WHERE f.model_name='tennis_score_baseline'
                  AND f.observed_at >= :cutoff AND f.observed_at <= :now
                ORDER BY f.observed_at, f.id
            """), {"cutoff": cutoff, "now": now}).mappings().all()

        reason_counts: Counter[str] = Counter()
        optional_unknown_groups: Counter[str] = Counter()
        optional_unknown_reasons: Counter[str] = Counter()
        group_ready: Counter[str] = Counter()
        player_prior_count = 0
        serve_prior_counts: Counter[str] = Counter()
        delay_seconds: dict[str, list[float]] = defaultdict(list)
        event_status: dict[str, dict[str, bool]] = {}
        per_example = []
        ready_count = 0
        missing_check_count = 0
        for row in rows:
            readiness = row["readiness"] or {}
            if not readiness:
                missing_check_count += 1
                reason_counts["prelabel_pit_readiness_not_recorded"] += 1
            certified = bool(readiness.get("certified"))
            ready_count += int(certified)
            event_id = str(row["event_id"])
            event = event_status.setdefault(event_id, {"ready": False, "ready_before_start": False})
            event["ready"] = event["ready"] or certified
            starts_at = row["scheduled_start_at"]
            if certified and starts_at and row["observed_at"] <= starts_at:
                event["ready_before_start"] = True
            validation = readiness.get("validation") or {}
            optional_unknown_groups.update(readiness.get("optional_unknown_groups") or ())
            optional_unknown_reasons.update((readiness.get("optional_unknown_reasons") or {}).values())
            failed_groups = set()
            for field in (
                "missing_sources", "future_sources", "missing_evidence",
                "evidence_timestamp_missing", "evidence_after_cutoff",
                "evidence_source_missing", "evidence_hash_missing",
                "evidence_object_missing", "evidence_object_hash_mismatch",
            ):
                failed_groups.update(validation.get(field) or ())
            player_prior_count += int(bool(readiness.get("player_history_fallback")))
            serve_prior_counts.update((readiness.get("serve_history_fallback") or {}).keys())
            for group in readiness.get("source_evidence", {}):
                if group == "player_metrics" and readiness.get("player_history_fallback"):
                    continue
                if group.startswith("serve_metrics_") and group[-1] in readiness.get("serve_history_fallback", {}):
                    continue
                if group not in failed_groups:
                    group_ready[group] += 1
            reason_counts.update(readiness.get("reasons") or ())
            for group, available_at in (readiness.get("source_available_at") or {}).items():
                if not available_at:
                    continue
                try:
                    available = datetime.fromisoformat(str(available_at).replace("Z", "+00:00"))
                    delay_seconds[group].append(max(0.0, (row["observed_at"] - available).total_seconds()))
                except ValueError:
                    continue
            per_example.append(json.dumps({
                "snapshot_id": str(row["id"]), "event_id": event_id,
                "observed_at": row["observed_at"].isoformat(),
                "scheduled_start_at": starts_at.isoformat() if starts_at else None,
                "readiness": readiness,
            }, sort_keys=True, ensure_ascii=False))

        examples_bytes = ("\n".join(per_example) + ("\n" if per_example else "")).encode()
        examples_sha = hashlib.sha256(examples_bytes).hexdigest()
        from .storage import ResearchStorage
        examples_key = ResearchStorage().archive_raw_bytes(
            "tennis-pit-coverage", f"rolling-{now.strftime('%Y%m%dT%H')}",
            examples_bytes, examples_sha,
        )
        if not examples_key:
            raise RuntimeError("pit_coverage_artifact_archive_failed")
        summary = {
            "report_version": "tennis_prelabel_pit_readiness_v2",
            "generated_at": now.isoformat(), "window_start": cutoff.isoformat(),
            "window_end": now.isoformat(), "lookback_hours": int(lookback_hours),
            "indexed_snapshot_keys": len(rows),
            "snapshot_source_coverage": {
                "prelabel_ready_snapshots": ready_count,
                "incomplete_snapshots": len(rows) - ready_count,
                "readiness_check_missing_snapshots": missing_check_count,
                "ready_snapshot_share": round(ready_count / len(rows), 6) if rows else 0.0,
                "ready_unique_events": sum(value["ready"] for value in event_status.values()),
                "ready_events_before_start": sum(value["ready_before_start"] for value in event_status.values()),
                "source_groups_ready_snapshots": dict(group_ready),
                "allowed_player_prior_snapshots": player_prior_count,
                "allowed_serve_prior_snapshots_by_side": dict(serve_prior_counts),
                "optional_unknown_groups": dict(optional_unknown_groups),
                "optional_unknown_reasons": dict(optional_unknown_reasons),
                "incomplete_reasons": dict(reason_counts),
                "source_delay_seconds_max": {key: max(values) for key, values in delay_seconds.items() if values},
                # A feature-ready snapshot is not a labeled or builder-accepted example.
                "certified_examples": 0,
                "label_status": "pending_until_official_result",
            },
            "targets": {}, "training_gate": {"training_allowed": False},
            "training_not_run": True,
            "per_example_source_results": {
                "raw_object_key": examples_key, "sha256": examples_sha,
                "examples": len(rows), "content_type": "jsonl",
            },
        }
        payload = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        with engine.begin() as conn:
            for state_key in ("latest_rolling_coverage", "rolling_coverage:" + now.strftime("%Y%m%dT%H")):
                conn.execute(text("""
                    INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                    VALUES ('tennis_pit',:state_key,CAST(:payload AS jsonb),now())
                    ON CONFLICT(source,state_key) DO UPDATE
                    SET state_json=excluded.state_json,updated_at=excluded.updated_at
                """), {"state_key": state_key, "payload": payload})
        return summary
    finally:
        engine.dispose()
