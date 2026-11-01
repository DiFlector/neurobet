"""Shared point-in-time checks for the tennis odds-free feature contract."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Iterable


TENNIS_PIT_EVIDENCE_POLICY = "tennis_pit_db_state_v2"


def content_addressed_object_matches_hash(content_hash: Any, raw_key: Any) -> bool:
    """Support both Fonbet JSON objects and compressed provider responses."""
    digest = str(content_hash or "").removeprefix("sha256:").lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return False
    if not isinstance(raw_key, str):
        return False
    filename = PurePosixPath(raw_key).name.lower()
    return filename in {f"{digest}.json", f"{digest}.json.gz"}


def validate_feature_source_availability(
    as_of: datetime,
    source_available_at: dict[str, Any],
    required_groups: Iterable[str],
    *,
    allowed_missing_groups: Iterable[str] = (),
) -> dict[str, Any]:
    """Validate that every required source observation was available by cutoff."""
    cutoff = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)
    allowed_missing = set(allowed_missing_groups)
    missing: list[str] = []
    future: list[str] = []
    for group in required_groups:
        value = source_available_at.get(group)
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                value = None
        if not isinstance(value, datetime):
            if group not in allowed_missing:
                missing.append(group)
            continue
        available = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        if available > cutoff:
            future.append(group)
    return {
        "certified": not missing and not future,
        "missing_sources": missing,
        "future_sources": future,
        "allowed_missing_sources": sorted(allowed_missing),
        "as_of": cutoff.isoformat(),
    }


def resolve_event_attribute_observations(observations: Iterable[Any], as_of: datetime) -> dict[str, Any]:
    """Choose the newest immutable format/surface observation available at a cutoff."""
    cutoff = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)
    selected: dict[str, Any] = {}
    for row in observations:
        name = getattr(row, "attribute_name", None)
        available = getattr(row, "available_at", None)
        if isinstance(row, dict):
            name, available = row.get("attribute_name"), row.get("available_at")
        if name not in {"best_of", "surface"} or not isinstance(available, datetime):
            continue
        available = available.replace(tzinfo=timezone.utc) if available.tzinfo is None else available.astimezone(timezone.utc)
        identity_at = row.get("identity_available_at") if isinstance(row, dict) else getattr(row, "identity_available_at", None)
        if isinstance(identity_at, datetime):
            identity_at = identity_at.replace(tzinfo=timezone.utc) if identity_at.tzinfo is None else identity_at.astimezone(timezone.utc)
            available = max(available, identity_at)
        if available > cutoff:
            continue
        current = selected.get(name)
        current_at = getattr(current, "available_at", None) if current is not None else None
        if isinstance(current, dict):
            current_at = current.get("available_at")
        if current_at is not None:
            current_at = current_at.replace(tzinfo=timezone.utc) if current_at.tzinfo is None else current_at.astimezone(timezone.utc)
        row_id = str(row.get("id", "")) if isinstance(row, dict) else str(getattr(row, "id", ""))
        current_id = str(current.get("id", "")) if isinstance(current, dict) else str(getattr(current, "id", ""))
        if current is None or (available, row_id) > (current_at, current_id):
            selected[name] = row
    return selected


def validate_feature_source_evidence(
    as_of: datetime,
    source_available_at: dict[str, Any],
    source_evidence: dict[str, Any],
    required_groups: Iterable[str],
) -> dict[str, Any]:
    """Require timely provenance; retained Fonbet DB states need no raw archive."""
    required = tuple(required_groups)
    timing = validate_feature_source_availability(as_of, source_available_at, required)
    cutoff = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)
    missing: list[str] = []
    timestamp_missing: list[str] = []
    future: list[str] = []
    source_missing: list[str] = []
    hash_missing: list[str] = []
    object_missing: list[str] = []
    object_hash_mismatch: list[str] = []
    for group in required:
        evidence = source_evidence.get(group)
        if isinstance(evidence, dict):
            entries = [evidence] if any(key in evidence for key in ("available_at", "source", "snapshot_id")) else list(evidence.values())
        elif isinstance(evidence, list):
            entries = evidence
        else:
            entries = []
        if not entries or any(not isinstance(item, dict) for item in entries):
            missing.append(group)
            continue
        for item in entries:
            available = item.get("available_at")
            if isinstance(available, str):
                try:
                    available = datetime.fromisoformat(available.replace("Z", "+00:00"))
                except ValueError:
                    available = None
            if not isinstance(available, datetime):
                timestamp_missing.append(group)
            else:
                available = available.replace(tzinfo=timezone.utc) if available.tzinfo is None else available.astimezone(timezone.utc)
                if available > cutoff:
                    future.append(group)
            identity_at = item.get("identity_available_at")
            if identity_at is not None:
                if isinstance(identity_at, str):
                    try:
                        identity_at = datetime.fromisoformat(identity_at.replace("Z", "+00:00"))
                    except ValueError:
                        identity_at = None
                if not isinstance(identity_at, datetime):
                    timestamp_missing.append(group)
                else:
                    identity_at = identity_at.replace(tzinfo=timezone.utc) if identity_at.tzinfo is None else identity_at.astimezone(timezone.utc)
                    if identity_at > cutoff:
                        future.append(group)
            if not item.get("source") and not item.get("snapshot_id"):
                source_missing.append(group)
            # The persisted Fonbet state is an accepted primary observation.
            # Only its archive requirement is waived; timing and the snapshot
            # reference remain mandatory. Other feature sources stay strict.
            if (group == "event_state" and item.get("snapshot_id")
                    and item.get("source") in {
                        "fonbet_event_state_snapshot", "fonbet_archived_live_snapshot",
                    }):
                continue
            if (group == "player_metrics" and item.get("source") == "unknown_player_history_v1"
                    and item.get("history_status") == "unknown"
                    and item.get("observation_kind") == "contract_prior"):
                continue
            if (group in {"serve_metrics_a", "serve_metrics_b"}
                    and item.get("source") == "unknown_serve_history_v1"
                    and item.get("observation_kind") == "contract_prior"
                    and item.get("history_status") == "unknown"):
                continue
            # evidence_hash is a hash of our projection envelope; it cannot prove
            # the bytes received from the upstream source. Require source bytes
            # (or an immutable digest of the causal profile history) instead.
            if not any(item.get(field) for field in ("raw_hash", "content_hash", "history_digest")):
                hash_missing.append(group)
            if group in {"event_state", "event_format", "event_surface"} and not item.get("raw_object_key"):
                object_missing.append(group)
            elif group in {"event_state", "event_format", "event_surface"}:
                source_hash = item.get("raw_hash") or item.get("content_hash")
                if source_hash:
                    if not content_addressed_object_matches_hash(source_hash, item["raw_object_key"]):
                        object_hash_mismatch.append(group)
    return {
        **timing,
        "evidence_policy": TENNIS_PIT_EVIDENCE_POLICY,
        "certified": timing["certified"] and not any((
            missing, timestamp_missing, future, source_missing, hash_missing,
            object_missing, object_hash_mismatch,
        )),
        "missing_evidence": sorted(set(missing)),
        "evidence_timestamp_missing": sorted(set(timestamp_missing)),
        "evidence_after_cutoff": sorted(set(future)),
        "evidence_source_missing": sorted(set(source_missing)),
        "evidence_hash_missing": sorted(set(hash_missing)),
        "evidence_object_missing": sorted(set(object_missing)),
        "evidence_object_hash_mismatch": sorted(set(object_hash_mismatch)),
    }


PLAYER_HISTORY_FALLBACK_POLICY = "unknown_player_history_v1"


def resolve_unknown_player_history(as_of, source_available_at, source_evidence, feature_values=None):
    """Use explicit neutral player priors when historical inputs cannot be verified.

    This is a missing-data policy, not evidence that the real player is a debutant.
    Serve evidence is intentionally independent and is never synthesized here.
    """
    sources, evidence = dict(source_available_at), dict(source_evidence)
    values = dict(feature_values) if feature_values is not None else None
    original = evidence.get("player_metrics")
    entries = (original if isinstance(original, dict) and any(side in original for side in ("a", "b")) else
        ({side: original for side in ("a", "b")} if isinstance(original, dict) and "source" in original else
        (dict(zip(("a", "b"), original)) if isinstance(original, list) else {})))
    entries = dict(entries)
    fallback = {}
    for side in ("a", "b"):
        item = entries.get(side)
        item = item if isinstance(item, dict) else {}
        timing = item.get("available_at") if isinstance(item, dict) else None
        check = validate_feature_source_evidence(as_of, {"player_metrics": timing},
                    {"player_metrics": [item]}, ("player_metrics",))
        identity_valid = not (
            item.get("source") == "confirmed_flashscore_player_metrics" and not (
                item.get("source_player_id") and item.get("identity_available_at"))) and not (
            item.get("source") == "tennis_player_metric_snapshot" and not item.get("identity_link_id"))
        if check["certified"] and identity_valid and item.get("source") != PLAYER_HISTORY_FALLBACK_POLICY:
            continue
        fallback[side] = (item.get("reason") if isinstance(item, dict) else None) or (
            "history_or_identity_unavailable" if not timing else "history_evidence_unverifiable")
        entries[side] = {"source": PLAYER_HISTORY_FALLBACK_POLICY,
            "available_at": as_of.isoformat(), "history_status": "unknown",
            "observation_kind": "contract_prior", "reason": fallback[side],
            "discarded_source_evidence": item.get("discarded_source_evidence", item)}
        if values is not None:
            values.update({f"elo_{side}": 1500.0, f"win_rate_90d_{side}": 0.5,
                           f"match_count_{side}": 0, f"player_metrics_missing_{side}": 1.0})
    if values is not None and fallback:
        for stem in ("elo", "win_rate_90d"):
            if all(isinstance(values.get(f"{stem}_{side}"), (int, float)) for side in ("a", "b")):
                values[f"{stem}_diff"] = values[f"{stem}_a"] - values[f"{stem}_b"]
    evidence["player_metrics"] = entries
    sources["player_metrics"] = as_of.isoformat() if fallback else sources.get("player_metrics")
    return sources, evidence, values, fallback


SERVE_HISTORY_FALLBACK_POLICY = "unknown_serve_history_v1"


def resolve_unknown_serve_history(as_of, source_available_at, source_evidence, feature_values=None):
    """Represent absent/unverifiable serving data by a fixed prior with zero sample weight."""
    sources, evidence = dict(source_available_at), dict(source_evidence)
    values = dict(feature_values) if feature_values is not None else None
    fallback = {}
    for side in ("a", "b"):
        group = "serve_metrics_" + side
        original = evidence.get(group)
        entries = original if isinstance(original, list) else [original]
        check = validate_feature_source_evidence(as_of, sources, evidence, (group,))
        prior = any(isinstance(item, dict) and item.get("source") == SERVE_HISTORY_FALLBACK_POLICY
                    for item in entries)
        no_sample = values is not None and values.get("serve_sample_weight_" + side) == 0
        no_denominator = any(isinstance(item, dict) and "serve_points" in item
                             and not item["serve_points"] for item in entries)
        if check["certified"] and not (prior or no_sample or no_denominator):
            continue
        reason = "source_after_cutoff" if check["future_sources"] or check["evidence_after_cutoff"] else (
            "no_observed_serving_sample" if no_sample or no_denominator else "serve_evidence_unavailable")
        if prior:
            reason = next((item.get("reason") for item in entries if isinstance(item, dict)
                           and item.get("source") == SERVE_HISTORY_FALLBACK_POLICY and item.get("reason")), reason)
        fallback[side] = reason
        # Repeated materialization must not nest prior envelopes indefinitely.
        discarded = (entries[0].get("discarded_source_evidence")
                     if prior and isinstance(entries[0], dict) else original)
        evidence[group] = [{"source": SERVE_HISTORY_FALLBACK_POLICY,
            "available_at": as_of.isoformat(), "history_status": "unknown",
            "observation_kind": "contract_prior", "reason": reason,
            "discarded_source_evidence": discarded}]
        sources[group] = as_of.isoformat()
        if values is not None:
            values["serve_probability_" + side] = 0.62
            values["serve_sample_weight_" + side] = 0.0
    return sources, evidence, values, fallback
