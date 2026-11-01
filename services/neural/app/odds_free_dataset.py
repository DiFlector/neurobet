"""Fail-closed materialization for the tennis odds-free feature contract.

This module only validates and serializes examples. It never fits a model,
opens a model registry, or writes to the trainer's priced-snapshot dataset.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import math
from typing import Any, Iterable

from .tennis_forecast import (
    ODDS_FREE_FEATURE_NAMES,
    ODDS_FREE_FEATURE_SOURCE_GROUPS,
    ODDS_FREE_FEATURE_VERSION,
    odds_free_feature_contract,
)
from contracts.tennis_pit import TENNIS_PIT_EVIDENCE_POLICY, validate_feature_source_evidence, resolve_unknown_player_history, resolve_unknown_serve_history


ODDS_FREE_DATASET_BUILDER_VERSION = "tennis_odds_free_dataset_v2_optional_serve_v1"
_FEATURE_GROUP_BY_NAME = {
    feature: group
    for group, features in ODDS_FREE_FEATURE_SOURCE_GROUPS.items()
    for feature in features
}


def _utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def build_odds_free_dataset_example(example: dict[str, Any], *, dataset_as_of: datetime) -> dict[str, Any]:
    """Validate one candidate and return either a complete vector or reasons.

    V2 encodes unavailable player and serving history with explicit priors and
    missingness masks. State and known-format inputs retain their checks.
    """
    if not isinstance(example, dict):
        raise TypeError("example_must_be_a_mapping")
    cutoff = _utc_datetime(dataset_as_of)
    if cutoff is None:
        raise ValueError("dataset_as_of_must_be_a_datetime")

    availability = example.get("feature_availability")
    availability = availability if isinstance(availability, dict) else {}
    feature_contract_version = availability.get("contract_version", ODDS_FREE_FEATURE_VERSION)
    contract = odds_free_feature_contract(feature_contract_version)
    feature_names = contract["names"]
    feature_groups = contract["source_groups"]
    source_available_at = availability.get("source_available_at")
    source_available_at = source_available_at if isinstance(source_available_at, dict) else {}
    source_evidence = availability.get("source_evidence")
    source_evidence = source_evidence if isinstance(source_evidence, dict) else {}
    contract_defaults = set(availability.get("contract_defaulted_sources") or [])
    resolution = availability.get("source_resolution")
    resolution = resolution if isinstance(resolution, dict) else {}
    retrospective_sources = set(availability.get("retrospective_sources") or [])
    reasons: list[str] = []
    for group in sorted(set(source_available_at) - set(feature_groups)):
        reasons.append(f"source_group_not_in_contract:{group}")

    snapshot_at = _utc_datetime(example.get("snapshot_observed_at"))
    if snapshot_at is None:
        reasons.append("snapshot_timestamp_missing")
        snapshot_at = cutoff
    values = example.get("feature_values")
    values = values if isinstance(values, dict) else {}
    player_history_fallback = {}
    serve_history_fallback = {}
    if feature_contract_version == ODDS_FREE_FEATURE_VERSION:
        source_available_at, source_evidence, values, player_history_fallback = resolve_unknown_player_history(
            snapshot_at, source_available_at, source_evidence, values)
        contract_defaults.discard("player_metrics")
        source_available_at, source_evidence, values, serve_history_fallback = resolve_unknown_serve_history(
            snapshot_at, source_available_at, source_evidence, values)
        for side in serve_history_fallback:
            contract_defaults.discard("serve_metrics_" + side)
            retrospective_sources.discard("serve_metrics_" + side)
        format_known = values.get("match_format_known")
        known = format_known is True or (isinstance(format_known, (int, float))
                                         and not isinstance(format_known, bool) and float(format_known) == 1.0)
        unknown = format_known is False or (isinstance(format_known, (int, float))
                                            and not isinstance(format_known, bool) and float(format_known) == 0.0)
        if not (known or unknown):
            reasons.append("feature_value_invalid:match_format_known")
        if known and values.get("sets_to_win") not in (2, 3, 2.0, 3.0):
            reasons.append("feature_value_invalid:sets_to_win")
        if unknown and values.get("sets_to_win") != 0:
            reasons.append("unknown_format_must_use_zero_sets_to_win")
        required_groups = tuple(group for group in feature_groups if group != "event_format" or known)
    else:
        required_groups = tuple(feature_groups)
    pit = validate_feature_source_evidence(
        snapshot_at, source_available_at, source_evidence, required_groups
    )
    for group in pit["missing_sources"]:
        reasons.append(f"pit_source_missing:{group}")
    for group in pit["future_sources"]:
        reasons.append(f"pit_source_after_cutoff:{group}")
    for group in sorted(contract_defaults):
        if group in feature_groups:
            reasons.append(f"contract_prior_not_allowed:{group}")
    for group in sorted(retrospective_sources):
        if group in feature_groups:
            reasons.append(f"retrospective_feature_source:{group}")
    for group, sides in resolution.items():
        if group not in feature_groups or (group in {"player_metrics", "serve_metrics_a", "serve_metrics_b"} and feature_contract_version == ODDS_FREE_FEATURE_VERSION):
            continue
        resolutions = sides.values() if isinstance(sides, dict) else (sides,)
        if any(str(value).startswith("contract_default") for value in resolutions):
            reasons.append(f"contract_prior_not_allowed:{group}")
    for group in pit["missing_evidence"]:
        reasons.append(f"pit_evidence_missing:{group}")
    for group in pit["evidence_timestamp_missing"]:
        reasons.append(f"pit_evidence_timestamp_missing:{group}")
    for group in pit["evidence_after_cutoff"]:
        reasons.append(f"pit_evidence_after_cutoff:{group}")
    for group in pit["evidence_source_missing"]:
        reasons.append(f"pit_evidence_source_missing:{group}")
    for group in pit["evidence_hash_missing"]:
        reasons.append(f"pit_evidence_hash_missing:{group}")
    for group in pit["evidence_object_missing"]:
        reasons.append(f"pit_evidence_object_missing:{group}")
    for group in pit["evidence_object_hash_mismatch"]:
        reasons.append(f"pit_evidence_object_hash_mismatch:{group}")

    label = example.get("label")
    if isinstance(label, bool) or not isinstance(label, int) or label not in (0, 1):
        reasons.append("label_missing_or_invalid")
    label_available_at = _utc_datetime(example.get("label_available_at"))
    if label_available_at is not None and label_available_at > cutoff:
        reasons.append("label_not_yet_available")

    if not isinstance(values, dict):
        values = {}
        reasons.append("feature_values_not_materialized")
    unknown_features = sorted(set(values) - set(feature_names))
    if unknown_features:
        reasons.append("feature_not_in_allowlist:" + ",".join(unknown_features))
    missing_features = [name for name in feature_names if name not in values]
    if missing_features:
        reasons.extend(f"feature_value_missing:{name}" for name in missing_features)
    vector: list[float] = []
    for name in feature_names:
        if name not in values:
            continue
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            reasons.append(f"feature_value_invalid:{name}")
            continue
        vector.append(float(value))
    if len(vector) != len(feature_names):
        vector = []
    feature_pit_reasons = [reason for reason in reasons if reason.startswith((
        "pit_", "contract_prior_not_allowed:", "retrospective_feature_source:",
        "source_group_not_in_contract:", "feature_value_missing:", "feature_value_invalid:",
        "feature_not_in_allowlist:", "unknown_format_must_use_zero_sets_to_win",
    ))]
    feature_pit_certified = not feature_pit_reasons and "snapshot_timestamp_missing" not in reasons
    label_valid = isinstance(label, int) and not isinstance(label, bool) and label in (0, 1)

    # Preserve the precise supplied source provenance rather than inferring
    # an availability timestamp from the event, competition, or score.
    record = {
        "builder_version": ODDS_FREE_DATASET_BUILDER_VERSION,
        "feature_contract": feature_contract_version,
        "pit_evidence_policy": TENNIS_PIT_EVIDENCE_POLICY,
        "feature_pit_certified": feature_pit_certified,
        "example_id": example.get("example_id") or ":".join(str(example.get(key) or "") for key in (
            "target", "fonbet_event_id", "snapshot_id", "point_version_id"
        )),
        "target": example.get("target"),
        "label": label if isinstance(label, int) and not isinstance(label, bool) else None,
        "label_available_at": label_available_at.isoformat() if label_available_at else None,
        "snapshot_observed_at": snapshot_at.isoformat() if "snapshot_timestamp_missing" not in reasons else None,
        "label_timing_status": "unknown" if label_valid and label_available_at is None else (
            "known" if label_available_at is not None else "invalid_or_missing_label"),
        "label_availability_time_known": label_valid and label_available_at is not None,
        "training_chronology": "requires_fold_boundary_evaluation",
        "feature_names": list(feature_names),
        "feature_values": vector if not reasons else None,
        "feature_source_groups": {name: next((group for group, names in feature_groups.items() if name in names), None)
                                  for name in feature_names},
        "source_available_at": source_available_at,
        "source_resolution": resolution,
        "player_history_fallback": player_history_fallback,
        "serve_history_fallback": serve_history_fallback,
        "optional_unknown_groups": availability.get("optional_unknown_groups") or [],
        "optional_unknown_reasons": availability.get("optional_unknown_reasons") or {},
        "source_evidence": source_evidence,
        "provenance": {
            key: example.get(key) for key in (
                "fonbet_event_id", "fonbet_source_event_id", "snapshot_id", "source_match_id",
                "point_document_id", "point_version_id", "match_link_id", "match_link_available_at",
                "result_time_source", "retrospective",
            ) if key in example
        },
        "status": "accepted" if not reasons else "rejected",
        "rejection_reasons": sorted(set(reasons)),
    }
    return record


def build_odds_free_dataset(examples: Iterable[dict[str, Any]], *, dataset_as_of: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build an auditable, untrained candidate set and summary."""
    rows = [build_odds_free_dataset_example(example, dataset_as_of=dataset_as_of) for example in examples]
    contracts = {row["feature_contract"] for row in rows}
    if len(contracts) > 1:
        raise ValueError("mixed_odds_free_feature_contracts_in_dataset")
    rejected = Counter(reason for row in rows for reason in row["rejection_reasons"])
    unknown_reasons = Counter(
        reason for row in rows for reason in row.get("optional_unknown_reasons", {}).values()
    )
    accepted_count = sum(row["status"] == "accepted" for row in rows)
    return rows, {
        "builder_version": ODDS_FREE_DATASET_BUILDER_VERSION,
        "feature_contract": rows[0]["feature_contract"] if rows else ODDS_FREE_FEATURE_VERSION,
        "pit_evidence_policy": TENNIS_PIT_EVIDENCE_POLICY,
        "input_examples": len(rows),
        "accepted_examples": accepted_count,
        "rejected_examples": len(rows) - accepted_count,
        "pit_certified_examples": sum(row["feature_pit_certified"] for row in rows),
        "feature_pit_certified": bool(rows) and all(row["feature_pit_certified"] for row in rows),
        "labels_with_unknown_availability_time": sum(row["label_timing_status"] == "unknown" for row in rows),
        "examples_with_known_label_availability_time": sum(row["label_availability_time_known"] for row in rows),
        "unknown_optional_feature_reasons": dict(sorted(unknown_reasons.items())),
        "pit_certified": bool(rows) and all(row["feature_pit_certified"] for row in rows),
        "training_admission": "not_evaluated",
        "rejection_reason_counts": dict(sorted(rejected.items())),
    }
