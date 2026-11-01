"""Summarize feature/source availability in a saved odds-free audit manifest.

This is a local, read-only pass over the persisted JSONL evidence. It does not
rebuild the manifest, query production, train a model, or inspect holdout quality.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics


def _parse_time(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def audit(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    source_resolution = Counter()
    source_available = Counter()
    source_missing = Counter()
    player_observed_snapshots = set()
    player_defaulted_snapshots = set()
    target_counts = Counter()
    server_counts = Counter()
    result_time_sources = Counter()
    result_time_sources_by_event = defaultdict(set)
    result_time_sources_by_source_match = defaultdict(set)
    exact_rows = Counter()
    exact_state_values = defaultdict(set)
    timeline_keys = set()
    label_lags_seconds = []
    future_labels = 0
    future_label_rows = []
    for row in rows:
        target_counts[row.get("target", "unknown")] += 1
        availability = row.get("feature_availability") or {}
        if availability.get("certified"):
            source_resolution["pit_certified"] += 1
        for key, value in (availability.get("source_resolution") or {}).items():
            if isinstance(value, dict):
                for side, resolution in value.items():
                    source_resolution[f"{key}.{side}:{resolution}"] += 1
                    if key == "player_metrics" and resolution == "observed_snapshot":
                        player_observed_snapshots.add(row.get("snapshot_id"))
                    elif key == "player_metrics" and resolution == "contract_default":
                        player_defaulted_snapshots.add(row.get("snapshot_id"))
            else:
                source_resolution[f"{key}:{value}"] += 1
        for key, value in (availability.get("source_available_at") or {}).items():
            source_available[f"{key}:present" if value else f"{key}:missing"] += 1
        for source in availability.get("missing_sources") or []:
            source_missing[source] += 1
        for source in availability.get("contract_defaulted_sources") or []:
            source_resolution[f"{source}:contract_defaulted"] += 1
        server_counts["known" if row.get("source_point_server_known") else "unknown"] += 1
        result_time_source = row.get("result_time_source") or "missing"
        result_time_sources[result_time_source] += 1
        result_time_sources_by_event[row.get("fonbet_event_id")].add(result_time_source)
        result_time_sources_by_source_match[row.get("source_match_id")].add(result_time_source)
        exact_key = (row.get("snapshot_id"), row.get("target"), row.get("point_index"))
        exact_rows[exact_key] += 1
        exact_state_values[exact_key].add((tuple(row.get("game_score") or []), row.get("label")))
        timeline_keys.add((row.get("source_match_id"), row.get("set_number"), row.get("point_index"), row.get("target")))
        snapshot_at = _parse_time(row.get("snapshot_observed_at"))
        label_at = _parse_time(row.get("label_available_at"))
        if snapshot_at and label_at:
            lag = (label_at - snapshot_at).total_seconds()
            label_lags_seconds.append(lag)
            future_labels += int(lag < 0)
            if lag < 0:
                future_label_rows.append({
                    "source_match_id": row.get("source_match_id"),
                    "fonbet_event_id": row.get("fonbet_event_id"),
                    "target": row.get("target"),
                    "snapshot_observed_at": row.get("snapshot_observed_at"),
                    "label_available_at": row.get("label_available_at"),
                    "lag_seconds": lag,
                })

    repeated_exact_rows = sum(count - 1 for count in exact_rows.values() if count > 1)
    duplicate_state_groups = sum(count > 1 for count in exact_rows.values())
    conflicting_state_groups = sum(len(values) > 1 for values in exact_state_values.values())
    sorted_lags = sorted(label_lags_seconds)
    def percentile(q: float):
        if not sorted_lags:
            return None
        return sorted_lags[min(len(sorted_lags) - 1, round((len(sorted_lags) - 1) * q))]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest": str(path),
        "examples": len(rows),
        "holdout_quality_metrics_opened": False,
        "targets": dict(target_counts),
        "pit_certified_examples": sum(bool((r.get("feature_availability") or {}).get("certified")) for r in rows),
        "source_resolution_counts": dict(sorted(source_resolution.items())),
        "source_timestamp_counts": dict(sorted(source_available.items())),
        "missing_sources": dict(sorted(source_missing.items())),
        "player_metric_snapshot_coverage": {
            "unique_snapshots_with_any_observed_side_metric": len(player_observed_snapshots),
            "unique_snapshots_with_any_defaulted_side_metric": len(player_defaulted_snapshots),
        },
        "point_server": dict(server_counts),
        "result_time_source_examples": dict(result_time_sources),
        "result_time_source_unique_events": dict(Counter(
            next(iter(sources)) if len(sources) == 1 else "conflicting_sources"
            for event_id, sources in result_time_sources_by_event.items() if event_id is not None
        )),
        "result_time_source_unique_source_matches": dict(Counter(
            next(iter(sources)) if len(sources) == 1 else "conflicting_sources"
            for sources in result_time_sources_by_source_match.values()
        )),
        "duplicate_checks": {
            "exact_snapshot_target_index_extra_rows": repeated_exact_rows,
            "source_match_set_point_target_timeline_keys_reused_across_snapshots": len(rows) - len(timeline_keys),
            "exact_snapshot_target_index_duplicate_groups": duplicate_state_groups,
            "conflicting_state_groups": conflicting_state_groups,
            "note": "exact duplicate/conflict checks use a saved forecast snapshot; timeline-key reuse across later snapshots is expected",
        },
        "snapshot_to_label_availability_lag_seconds": {
            "examples_with_both_timestamps": len(sorted_lags),
            "future_label_examples": future_labels,
            "min": min(sorted_lags) if sorted_lags else None,
            "median": statistics.median(sorted_lags) if sorted_lags else None,
            "p95": percentile(0.95),
            "max": max(sorted_lags) if sorted_lags else None,
            "future_label_examples": future_label_rows,
            "interpretation": "forecast snapshot to label availability lag, not provider ingestion latency",
        },
        "latency_limit": "provider retrieved_at/received_at pairs are not present in this manifest, so true source-to-ingest latency cannot be computed from these rows",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.manifest)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
