"""Point-in-time label audit for numeric Fonbet singles tennis events."""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import chain
from datetime import datetime, timedelta, timezone
import re
import hashlib
import json
import os
import math
import uuid
from sqlalchemy import text, tuple_

from db.connection import SessionLocal
from db.models import (Event, EventStateSnapshot, ExtTennisMatch, League,
    TennisPlayerIdentityLink, TennisPlayerMetricSnapshot, TennisPlayerProfile, TennisPointDocument,
    TennisPointHistoryVersion, TennisPointMatchLink)
from db.models.external_tennis import TennisEventAttributeObservation
from contracts.tennis_pit import (resolve_event_attribute_observations,
                                   validate_feature_source_availability,
                                   validate_feature_source_evidence, resolve_unknown_player_history, resolve_unknown_serve_history)
from tennis_adapter.point_history import is_authoritative_point_history_version
from tennis_adapter.history_linking import official_result
from tennis_result_observation import score_signature


PIT_FEATURE_CONTRACT_VERSION = "tennis_odds_free_v2"
PIT_FEATURE_SOURCE_GROUPS = (
    "player_metrics", "serve_metrics_a", "serve_metrics_b", "event_state", "event_format",
)
SNAPSHOT_LOOKUP_BATCH_SIZE = 100
SNAPSHOT_HISTORY_LOOKUP_BATCH_SIZE = 10
ODDS_FREE_V2_FEATURE_NAMES = (
    "elo_a", "elo_b", "elo_diff", "win_rate_90d_a", "win_rate_90d_b", "win_rate_90d_diff",
    "match_count_a", "match_count_b", "player_metrics_missing_a", "player_metrics_missing_b",
    "serve_probability_a", "serve_probability_b", "serve_sample_weight_a", "serve_sample_weight_b",
    "sets_a", "sets_b", "games_a", "games_b", "points_a", "points_b",
    "server_a", "server_b", "server_unknown", "is_tiebreak", "sets_to_win", "match_format_known",
)


def _missing_feature_values(values: dict | None) -> list[str]:
    if not isinstance(values, dict):
        return list(ODDS_FREE_V2_FEATURE_NAMES)
    return [name for name in ODDS_FREE_V2_FEATURE_NAMES if name not in values or values[name] is None]


def _example_rejection_reasons(row):
    reasons=[]
    if row.get('feature_availability',{}).get('certified') is not True:
        reasons.append('feature_pit_not_certified')
    values=row.get('feature_values') or {}
    reasons.extend('feature_value_missing:'+name for name in _missing_feature_values(values))
    for name,value in values.items():
        if name in ODDS_FREE_V2_FEATURE_NAMES and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)):
            reasons.append('feature_value_invalid:'+name)
    try:
        snapshot_at=datetime.fromisoformat(row['snapshot_observed_at'].replace('Z','+00:00'))
        if snapshot_at.tzinfo is None:
            raise ValueError('snapshot_timestamp_without_timezone')
        if row.get('label_available_at'):
            label_at=datetime.fromisoformat(row['label_available_at'].replace('Z','+00:00'))
            if label_at.tzinfo is None or snapshot_at>=label_at:
                reasons.append('snapshot_not_before_label')
    except (KeyError,TypeError,ValueError):
        reasons.append('snapshot_timestamp_missing_or_invalid')
    return reasons


def _usable_training_example(row):
    """Unknown-time labels may remain evaluation candidates, never causal train."""
    return not _example_rejection_reasons(row)


def _read_saved_snapshot_cohort(path: str) -> dict:
    """Treat a prior manifest only as an index of DB keys, never as evidence."""
    with open(path, "rb") as handle:
        payload = handle.read()
    keys = {}
    event_ids = set()
    examples = 0
    for number, line in enumerate(payload.splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        event_id = uuid.UUID(row["fonbet_event_id"])
        event_ids.add(event_id)
        examples += 1
        for source in (row, row.get("result_time_snapshot_evidence") or {}):
            snapshot_id = source.get("snapshot_id")
            raw_at = source.get("snapshot_observed_at") or source.get("observed_at")
            if not snapshot_id or not raw_at:
                continue
            observed_at = datetime.fromisoformat(raw_at.replace("Z", "+00:00"))
            if observed_at.tzinfo is None:
                raise ValueError(f"snapshot_index_timestamp_without_timezone:line_{number}")
            key = (uuid.UUID(snapshot_id), observed_at.astimezone(timezone.utc))
            if key in keys and keys[key] != event_id:
                raise ValueError("snapshot_index_conflicting_event_id")
            keys[key] = event_id
    return {"path": path, "sha256": hashlib.sha256(payload).hexdigest(),
            "examples": examples, "event_ids": event_ids, "keys": keys}


def _load_saved_snapshot_cohort(session, cohort: dict) -> list:
    """Fetch JSON only for exact (id, time) primary keys in bounded batches."""
    keys = sorted(cohort["keys"], key=lambda item: (item[1], str(item[0])))
    rows = []
    for start in range(0, len(keys), 100):
        batch = keys[start:start + 100]
        found = session.execute(text("""
            WITH requested AS (
                SELECT * FROM unnest(CAST(:ids AS uuid[]), CAST(:times AS timestamptz[]), CAST(:events AS uuid[]))
                AS r(id, observed_at, event_id)
            )
            SELECT s.id,s.observed_at,s.event_id,s.score_detail,s.sport_state,
                   s.current_period,COALESCE(s.raw_hash,e.raw_content_hash) AS raw_hash,
                   COALESCE(s.raw_object_key,e.raw_object_key) AS raw_object_key,
                   s.status,s.score,e.id AS raw_evidence_id,e.available_at AS raw_evidence_available_at,
                   e.evidence_hash AS raw_evidence_hash,e.evidence_json AS raw_evidence_json
            FROM requested r JOIN LATERAL (
                SELECT id,observed_at,event_id,score_detail,sport_state,current_period,
                       raw_hash,raw_object_key,status,score
                FROM event_state_snapshots
                WHERE id=r.id AND observed_at=r.observed_at AND event_id=r.event_id
                  AND sport_code='tennis' AND observed_at >= :lower AND observed_at <= :upper
                LIMIT 1
            ) s ON TRUE
            LEFT JOIN LATERAL (
                SELECT id,available_at,raw_content_hash,raw_object_key,evidence_hash,evidence_json
                FROM tennis_event_state_raw_evidence
                WHERE snapshot_id=s.id AND snapshot_observed_at=s.observed_at AND event_id=s.event_id
                  AND available_at <= s.observed_at
                ORDER BY available_at DESC,id DESC LIMIT 1
            ) e ON TRUE
        """), {"ids": [key[0] for key in batch], "times": [key[1] for key in batch],
                 "events": [cohort["keys"][key] for key in batch],
                 "lower": batch[0][1], "upper": batch[-1][1]}).all()
        for row in found:
            if cohort["keys"][(row.id, row.observed_at)] != row.event_id:
                raise ValueError("snapshot_index_database_event_mismatch")
        rows.extend(found)
    return rows


def _load_result_snapshot_keys(session, cohort):
    """Read receipt witnesses without loading feature JSON or raw-evidence joins."""
    keys = sorted(cohort["keys"], key=lambda item: (item[1], str(item[0])))
    rows = []
    for start in range(0, len(keys), 100):
        batch = keys[start:start+100]
        rows.extend(session.execute(text("""
            WITH requested AS (
                SELECT * FROM unnest(CAST(:ids AS uuid[]),CAST(:times AS timestamptz[]),CAST(:events AS uuid[]))
                AS r(id,observed_at,event_id)
            )
            SELECT s.id,s.event_id,s.observed_at,s.score,s.status
            FROM requested r JOIN LATERAL (
                SELECT id,event_id,source_event_id,observed_at,score,status FROM event_state_snapshots
                WHERE id=r.id AND event_id=r.event_id AND observed_at=r.observed_at
                  AND observed_at>=:lower AND observed_at<=:upper AND sport_code='tennis'
                LIMIT 1
            ) s ON TRUE
            JOIN events e ON e.id=r.event_id AND e.source='fonbet'
                         AND e.source_event_id=s.source_event_id
        """), {"ids": [key[0] for key in batch], "times": [key[1] for key in batch],
               "events": [cohort["keys"][key] for key in batch],
               "lower": batch[0][1], "upper": batch[-1][1]}).all())
    return rows


def _history_versions_by_id(session, ordered_ids, batch_size=50):
    """Load linked JSONB by primary key without sorting the global JSONB corpus."""
    for start in range(0, len(ordered_ids), batch_size):
        keys = ordered_ids[start:start + batch_size]
        rows = session.query(TennisPointHistoryVersion).filter(
            TennisPointHistoryVersion.id.in_(keys)).all()
        by_id = {row.id: row for row in rows}
        if set(by_id) != set(keys):
            raise ValueError("history_version_primary_key_missing")
        for key in keys:
            yield by_id[key]


def _direct_api_context(version, document, event, cutoff):
    quality = version.quality_json or {}
    link = quality.get('link') or {}
    proofs = link.get('canonical_identity_proofs') or []
    if (document.source != 'api_tennis' or version.parser_version != 'api_tennis_pbp_link_v2'
            or version.fonbet_event_id != event.id or str(link.get('fonbet_event_id')) != str(event.id)
            or document.source_match_id != version.source_match_id
            or link.get('source_match_id') != version.source_match_id
            or link.get('method') != 'confirmed_canonical_and_provider_ids_v2'
            or link.get('normalized_to_fonbet') is not True
            or quality.get('coordinate_schema') != 'api_pbp_coordinates_v2'
            or link.get('source_content_hash') != document.content_hash
            or version.available_at is None or document.available_at is None
            or version.available_at > cutoff or document.available_at > cutoff
            or len(proofs) != 2
            or [proof.get('participant_id') for proof in proofs] != [str(event.participant_a_id), str(event.participant_b_id)]
            or len({proof.get('canonical_id') for proof in proofs}) != 2
            or len(set(link.get('provider_alias_ids') or [])) != 2):
        return None
    try:
        for proof in proofs:
            for key in ('participant_id','canonical_id','identity_link_id','canonical_alias_id'):
                uuid.UUID(proof[key])
        for key in link['provider_alias_ids']:
            uuid.UUID(key)
        uuid.UUID(link['source_observation_id'])
        times = [datetime.fromisoformat(link[key]) for key in ('linked_at','source_observation_available_at')]
        times += [datetime.fromisoformat(proof[key]) for proof in proofs
                  for key in ('identity_available_at','alias_available_at')]
        provider_alias_times = link.get('provider_alias_available_at')
        if provider_alias_times is not None:
            if len(provider_alias_times) != 2:
                return None
            times += [datetime.fromisoformat(value) for value in provider_alias_times]
        if any(at.tzinfo is None or at > version.available_at for at in times):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return {'reference': 'api_tennis:'+version.source_match_id, 'source': 'api_tennis',
            'source_match_id': version.source_match_id, 'version': version, 'evidence': link}


def _load_direct_api_histories(session, eligible, cutoff):
    rows = session.query(TennisPointHistoryVersion, TennisPointDocument).join(
        TennisPointDocument, TennisPointDocument.id == TennisPointHistoryVersion.document_id).filter(
            TennisPointHistoryVersion.fonbet_event_id.in_(list(eligible)),
            TennisPointHistoryVersion.parser_version == 'api_tennis_pbp_link_v2',
            TennisPointHistoryVersion.available_at <= cutoff,
            TennisPointDocument.source == 'api_tennis').order_by(
                TennisPointHistoryVersion.available_at, TennisPointHistoryVersion.id).all() if eligible else []
    latest = {(version.fonbet_event_id, version.source_match_id): (version, document) for version, document in rows}
    contexts = []
    for (event_id, _), (version, document) in latest.items():
        context = _direct_api_context(version, document, eligible[event_id], cutoff)
        if context is None:
            continue
        proofs = context['evidence']['canonical_identity_proofs']
        valid = True
        for proof in proofs:
            identity = session.get(TennisPlayerIdentityLink, uuid.UUID(proof['identity_link_id']))
            if identity is None or identity.status != 'confirmed':
                valid = False;break
            current = session.query(TennisPlayerIdentityLink).filter(
                TennisPlayerIdentityLink.participant_id == identity.participant_id,
                TennisPlayerIdentityLink.source == identity.source,
                TennisPlayerIdentityLink.available_at <= cutoff).order_by(
                    TennisPlayerIdentityLink.available_at.desc(), TennisPlayerIdentityLink.id.desc()).first()
            if current is None or current.id != identity.id:
                valid = False;break
        if valid:
            from app.canonical_player_aliases import confirmed_player_alias_as_of
            proof_alias_ids = set()
            for proof in proofs:
                identity = session.get(TennisPlayerIdentityLink, uuid.UUID(proof['identity_link_id']))
                resolved = confirmed_player_alias_as_of(
                    session, source=identity.source, source_player_id=identity.source_player_id, as_of=cutoff)
                if (resolved is None or str(resolved[0].id) != proof['canonical_alias_id']
                        or str(resolved[0].canonical_player_id) != proof['canonical_id']
                        or resolved[1] > version.available_at):
                    valid = False; break
                proof_alias_ids.add(str(resolved[0].id))
            link_evidence = context['evidence']
            provider_ids = link_evidence.get('provider_player_ids') or []
            provider_alias_ids = link_evidence.get('provider_alias_ids') or []
            provider_canonicals = []
            if valid and len(provider_ids) == len(provider_alias_ids) == 2:
                for provider_id, expected_alias_id in zip(provider_ids, provider_alias_ids):
                    resolved = confirmed_player_alias_as_of(
                        session, source='api_tennis', source_player_id=provider_id, as_of=cutoff)
                    if (resolved is None or str(resolved[0].id) != expected_alias_id
                            or resolved[1] > version.available_at):
                        valid = False; break
                    provider_canonicals.append(str(resolved[0].canonical_player_id))
            elif valid:
                valid = False
            if valid:
                proof_canonicals = [proof['canonical_id'] for proof in proofs]
                first_is_a = link_evidence.get('source_first_is_fonbet_a') is True
                expected_provider_pair = proof_canonicals if first_is_a else proof_canonicals[::-1]
                valid = (proof_alias_ids == {proof['canonical_alias_id'] for proof in proofs}
                         and provider_canonicals == expected_provider_pair)
        if valid:
            contexts.append(context)
    return contexts


def _resolve_source_examples(provenance):
    groups = defaultdict(list)
    for key, row in provenance.items():
        if row['target'] == 'set':
            state = row.get('set_number')
        else:
            state = row.get('target_state') or (row['snapshot_id'], row['snapshot_observed_at'])
        groups[(row['target'], row['fonbet_event_id'], json.dumps(state, sort_keys=True))].append((key, row))
    kept, conflicts = {}, []
    for group, items in groups.items():
        if len({row['label'] for _, row in items}) > 1:
            conflicts.append({'target': group[0], 'fonbet_event_id': group[1],
                              'target_state': group[2], 'source_examples': [row for _, row in items]})
            continue
        predictions = defaultdict(list)
        for key, row in items:
            predictions[(row['snapshot_id'], row['snapshot_observed_at'])].append((key, row))
        for values in predictions.values():
            key, row = min(values, key=lambda pair: (pair[1].get('label_available_at') or '\uffff', repr(pair[0])))
            kept[key] = {**row, 'supporting_sources': sorted({value.get('source') or 'flashscore' for _, value in values})}
    return kept, conflicts


def _indexed_result_snapshot_evidence(events: dict, snapshots, *, cutoff=None) -> dict:
    """Revalidate exact saved final-score witnesses against current DB bytes."""
    evidence = {}
    for row in snapshots:
        event = events.get(row.event_id)
        if event is None or (cutoff is not None and row.observed_at > cutoff):
            continue
        official = (event.metadata_json or {}).get("fonbet_official_result") or {}
        signature = score_signature(official.get("final_score"))
        if not signature or row.score is None or not re.fullmatch(_score_token_pattern(signature), str(row.score)):
            continue
        previous = evidence.get(row.event_id)
        if previous is None or (row.observed_at, str(row.id)) < (previous["observed_at"], previous["snapshot_id"]):
            evidence[row.event_id] = {"snapshot_id": str(row.id), "observed_at": row.observed_at,
                                      "status": row.status, "score": row.score}
    return evidence


def _select_pre_result_snapshot(rows, *, feature_check, label_available_at, final_score=None):
    """Choose the best evidenced live prefix; never select a known result."""
    candidates = []
    final_signature = score_signature(final_score) if final_score else None
    for row in rows:
        if getattr(row, "status", "live") != "live":
            continue
        if label_available_at is not None and row.observed_at >= label_available_at:
            continue
        raw_score = getattr(row, "score", None)
        if final_signature and raw_score and score_signature(raw_score) == final_signature:
            continue
        check = feature_check(row)
        invalid_groups = set()
        for key in ("missing_sources", "future_sources", "missing_evidence", "evidence_timestamp_missing",
                    "evidence_after_cutoff", "evidence_source_missing", "evidence_hash_missing",
                    "evidence_object_missing", "evidence_object_hash_mismatch", "contract_defaulted_sources"):
            invalid_groups.update(check.get(key) or [])
        invalid_groups.update("feature:" + name for name in check.get("feature_missing_fields", []))
        candidates.append(((len(invalid_groups), row.observed_at, str(row.id)), row))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _event_state_source_evidence(snapshot_row, snapshot_id, snapshot_at):
    """Prefer an immutable archive witness; otherwise use current row provenance."""
    row = snapshot_row
    auxiliary_id = getattr(row, "raw_evidence_id", None) if row is not None else None
    available_at = getattr(row, "raw_evidence_available_at", None) if auxiliary_id else snapshot_at
    evidence = {
        "source": "fonbet_archived_live_snapshot" if auxiliary_id else "fonbet_event_state_snapshot",
        "snapshot_id": str(snapshot_id),
        "available_at": available_at.isoformat() if available_at else None,
        "raw_hash": getattr(row, "raw_hash", None) if row is not None else None,
        "raw_object_key": getattr(row, "raw_object_key", None) if row is not None else None,
    }
    if auxiliary_id:
        evidence["evidence_hash"] = getattr(row, "raw_evidence_hash", None)
        evidence["parser_version"] = (getattr(row, "raw_evidence_json", None) or {}).get("parser_version")
        evidence["raw_evidence_id"] = str(auxiliary_id)
    return evidence


def _write_training_examples(example_rows: list[dict], output_dir: str) -> dict:
    """Write deterministic JSONL and return both logical and byte-level hashes."""
    logical_payload = "\n".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) for row in example_rows
    ).encode("utf-8")
    logical_digest = hashlib.sha256(logical_payload).hexdigest()
    examples_path = os.path.join(
        output_dir, f"tennis_training_examples_{logical_digest[:12]}.jsonl"
    )
    os.makedirs(os.path.dirname(examples_path), exist_ok=True)
    temporary_path = examples_path + ".tmp"
    file_digest = hashlib.sha256()
    with open(temporary_path, "w", encoding="utf-8") as handle:
        for row in example_rows:
            rendered = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            handle.write(rendered)
            file_digest.update(rendered.encode("utf-8"))
    os.replace(temporary_path, examples_path)
    return {
        "path": examples_path,
        "examples": len(example_rows),
        "sha256": file_digest.hexdigest(),
        "logical_digest": logical_digest,
    }


def _write_snapshot_source_coverage(rows: list[dict], output_dir: str) -> dict:
    """Persist source outcomes for every indexed snapshot, including unlabelled live events."""
    logical = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )
    digest = hashlib.sha256(logical).hexdigest()
    path = os.path.join(output_dir, f"tennis_snapshot_source_coverage_{digest[:12]}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "wb") as handle:
        handle.write(logical)
    os.replace(temporary, path)
    return {"path": path, "examples": len(rows), "sha256": digest, "content_type": "jsonl"}


def _write_audit_jsonl(rows: list[dict], output_dir: str, prefix: str) -> dict:
    """Write sorted deterministic audit rows with a byte digest."""
    payload = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )
    digest = hashlib.sha256(payload).hexdigest()
    path = os.path.join(output_dir, f"{prefix}_{digest[:12]}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "wb") as handle:
        handle.write(payload)
    os.replace(temporary, path)
    return {"path": path, "rows": len(rows), "sha256": digest, "content_type": "jsonl"}


def _validate_feature_availability(as_of: datetime, source_available_at: dict,
                                  contract_defaults: set[str] | None = None,
                                  required_groups: tuple[str, ...] | None = None) -> dict:
    """Fail closed unless a source is causal or a documented contract prior is used."""
    contract_defaults = contract_defaults or set()
    if required_groups is None:
        # Unknown format is permitted; if a format source is supplied, validate
        # its timestamp rather than silently treating a late value as unknown.
        required_groups = tuple(group for group in PIT_FEATURE_SOURCE_GROUPS
                                if group != "event_format" or source_available_at.get(group) is not None)
    result = validate_feature_source_availability(
        as_of,
        source_available_at,
        required_groups,
        allowed_missing_groups=contract_defaults,
    )
    result["contract_defaulted_sources"] = sorted(contract_defaults)
    return result


def _latest_identity_link_as_of(rows, snapshot_at):
    """Resolve the newest append-only identity decision available at a cutoff."""
    return max(
        (row for row in rows if row.available_at <= snapshot_at),
        key=lambda row: (row.available_at, str(row.id)),
        default=None,
    )


def _summarize_feature_availability(checks: list[dict]) -> dict:
    """Separate causal contract priors from values observed in source data."""
    certified = [item for item in checks if item.get("certified") is True]
    defaulted = [item for item in checks if item.get("contract_defaulted_sources")]
    return {
        "checked_examples": len(checks),
        "certified_examples": len(certified),
        "uncertified_examples": len(checks) - len(certified),
        "fully_source_observed_examples": sum(
            item.get("certified") is True and not item.get("contract_defaulted_sources")
            and not item.get("player_history_fallback") and not item.get("serve_history_fallback") for item in checks
        ),
        "contract_defaulted_examples": len(defaulted),
        "allowed_player_prior_examples": sum(bool(item.get("player_history_fallback")) for item in checks),
        "allowed_serve_prior_examples": sum(bool(item.get("serve_history_fallback")) for item in checks),
    }


def _summarize_snapshot_source_coverage(checks: list[dict]) -> dict:
    """Count each source group using strict evidence and retain rejection causes."""
    result = _summarize_feature_availability(checks)
    groups = {name: {"available": 0, "contract_defaulted": 0, "missing_or_unverifiable": 0}
              for name in PIT_FEATURE_SOURCE_GROUPS}
    resolutions = Counter()
    exclusions = Counter()
    evidence_failures = (
        "missing_evidence", "evidence_timestamp_missing", "evidence_after_cutoff",
        "evidence_source_missing", "evidence_hash_missing", "evidence_object_missing",
        "evidence_object_hash_mismatch",
    )
    for check in checks:
        source_times = check.get("source_available_at", {})
        defaults = set(check.get("contract_defaulted_sources", []))
        unverifiable = {name for key in evidence_failures for name in check.get(key, [])}
        unverifiable.update(check.get("missing_sources", []))
        unverifiable.update(check.get("future_sources", []))
        optional_unknown = set(check.get("optional_unknown_groups", []))
        for name in PIT_FEATURE_SOURCE_GROUPS:
            if ((name == "player_metrics" and check.get("player_history_fallback")) or
                    (name.startswith("serve_metrics_") and name[-1] in check.get("serve_history_fallback", {}))):
                groups[name]["allowed_prior"] = groups[name].get("allowed_prior", 0) + 1
            elif name in defaults:
                groups[name]["contract_defaulted"] += 1
            elif name in optional_unknown:
                groups[name]["explicitly_unknown"] = groups[name].get("explicitly_unknown", 0) + 1
            elif source_times.get(name) is None or name in unverifiable:
                groups[name]["missing_or_unverifiable"] += 1
            else:
                groups[name]["available"] += 1
        for group, resolution in (check.get("source_resolution") or {}).items():
            if isinstance(resolution, dict):
                for side, value in resolution.items():
                    resolutions[f"{group}.{side}:{value}"] += 1
            else:
                resolutions[f"{group}:{resolution}"] += 1
        for key in evidence_failures + ("missing_sources", "future_sources"):
            for group in check.get(key, []):
                exclusions[f"{group}:{key}"] += 1
    result["source_groups"] = groups
    result["resolution_counts"] = dict(resolutions)
    result["exclusions"] = dict(exclusions)
    return result


def _materialize_odds_free_feature_values(event, snapshot, snapshot_at,
                                         metric_values_by_side, serve_values_by_side,
                                         event_attributes=None):
    """Materialize v2; unknown format and missingness are explicit feature values."""
    detail = snapshot.score_detail if isinstance(snapshot.score_detail, dict) else {}
    sport = snapshot.sport_state if isinstance(snapshot.sport_state, dict) else {}
    stats = detail.get("stats") if isinstance(detail.get("stats"), dict) else sport.get("stats", {})
    stats = stats if isinstance(stats, dict) else {}
    lines = stats.get("set_lines") if isinstance(stats.get("set_lines"), list) else []
    live_line = next((row for row in reversed(lines) if isinstance(row, dict) and row.get("live")), {})
    game = detail.get("current_game") if isinstance(detail.get("current_game"), dict) else {}
    values = {}

    side_features = {}
    for side in ("a", "b"):
        payload = metric_values_by_side.get(side)
        payload = payload if isinstance(payload, dict) else {}
        elo = payload.get("elo") if isinstance(payload.get("elo"), dict) else {}
        recent = payload.get("recent") if isinstance(payload.get("recent"), dict) else {}
        recent90 = recent.get("days_90") if isinstance(recent.get("days_90"), dict) else {}
        overall = payload.get("overall") if isinstance(payload.get("overall"), dict) else {}
        serve = serve_values_by_side.get(side)
        points, points_won = serve if serve else (0.0, 0.0)
        points, points_won = float(points or 0), float(points_won or 0)
        rate = ((points_won + 0.62 * 200.0) / (points + 200.0)) if points > 0 else 0.62
        side_features[side] = {
            "elo": float(elo.get("overall", 1500.0)),
            "win_rate": (float(recent90.get("wins", 0)) + 1.0) / (float(recent90.get("matches", 0)) + 2.0),
            "matches": int(overall.get("matches", 0) or 0),
            "missing": not bool(payload),
            "serve_rate": min(0.90, max(0.35, rate)),
            "serve_weight": min(0.95, points / (points + 200.0)) if points > 0 else 0.0,
        }
    a, b = side_features["a"], side_features["b"]
    values.update({
        "elo_a": a["elo"], "elo_b": b["elo"], "elo_diff": a["elo"] - b["elo"],
        "win_rate_90d_a": a["win_rate"], "win_rate_90d_b": b["win_rate"],
        "win_rate_90d_diff": a["win_rate"] - b["win_rate"],
        "match_count_a": a["matches"], "match_count_b": b["matches"],
        "player_metrics_missing_a": a["missing"], "player_metrics_missing_b": b["missing"],
        "serve_probability_a": a["serve_rate"], "serve_probability_b": b["serve_rate"],
        "serve_sample_weight_a": a["serve_weight"], "serve_sample_weight_b": b["serve_weight"],
    })
    try:
        values.update({"games_a": int(live_line["games_a"]), "games_b": int(live_line["games_b"])})
        values.update({"sets_a": int(stats.get("sets_a", detail.get("sets_a", 0))),
                       "sets_b": int(stats.get("sets_b", detail.get("sets_b", 0)))})
        tie = bool(game.get("is_tiebreak") or sport.get("tiebreak") or
                   (values["games_a"] == values["games_b"] == 6))
        if tie:
            values["points_a"], values["points_b"] = int(game.get("points_a")), int(game.get("points_b"))
        else:
            pa, pb = _point_order(game.get("points_a")), _point_order(game.get("points_b"))
            if pa is None or pb is None:
                raise ValueError("point_score_missing")
            values["points_a"], values["points_b"] = pa, pb
        server = detail.get("server") or sport.get("server")
        server = {"player_a": "a", "player_b": "b", "a": "a", "b": "b"}.get(str(server).casefold())
        values.update({"server_a": server == "a", "server_b": server == "b",
                       "server_unknown": server not in {"a", "b"}, "is_tiebreak": tie})
    except (KeyError, TypeError, ValueError):
        for key in ("sets_a", "sets_b", "games_a", "games_b", "points_a", "points_b",
                    "server_a", "server_b", "server_unknown", "is_tiebreak"):
            values.pop(key, None)

    metadata = dict(event.metadata_json) if isinstance(event.metadata_json, dict) else {}
    for attr, observation in (event_attributes or {}).items():
        metadata[attr] = observation.value_json.get("value")
        metadata[f"{attr}_available_at"] = observation.available_at.isoformat()
    raw_format_at = metadata.get("best_of_available_at")
    try:
        format_at = datetime.fromisoformat(str(raw_format_at).replace("Z", "+00:00")) if raw_format_at else None
        if format_at and format_at.tzinfo is None:
            format_at = format_at.replace(tzinfo=timezone.utc)
        best_of = int(metadata.get("best_of"))
        if best_of not in (3, 5) or format_at is None or format_at > snapshot_at:
            best_of = None
    except (TypeError, ValueError, OverflowError):
        best_of = None
    values["sets_to_win"] = best_of // 2 + 1 if best_of is not None else 0
    values["match_format_known"] = best_of is not None
    return {key: float(value) if isinstance(value, bool) else value for key, value in values.items()}


def _is_finished_set(a: int, b: int) -> bool:
    high, low = max(a, b), min(a, b)
    return (high >= 6 and high - low >= 2) or (high == 7 and low == 6)


def _tour(text: str, metadata: dict | None = None, external_tour: str | None = None) -> str:
    metadata = metadata if isinstance(metadata, dict) else {}
    official = metadata.get("fonbet_official_result") if isinstance(metadata.get("fonbet_official_result"), dict) else {}
    explicit = " ".join(str(official.get(key) or metadata.get(key) or "") for key in
                         ("tour", "category", "circuit", "age_group", "tournament_type"))
    value = f"{text} {explicit} {external_tour or ''}".casefold()
    if any(token in value for token in ("junior", "juniors", "юниор", "юнош", "u14", "u16", "u18", "boys", "girls")) \
            or re.search(r"\bj(?:30|60|100|200|300|500)\b", value):
        return "junior"
    if "challenger" in value or "челленджер" in value:
        return "challenger"
    if "itf" in value or "futures" in value or "фьючерс" in value:
        return "itf"
    if "wta" in value:
        return "wta"
    if "atp" in value:
        return "atp"
    return "unknown"


def _tour_source(text: str, metadata: dict | None = None, external_tour: str | None = None) -> str:
    """Describe which source supplied the retrospective competition category."""
    metadata = metadata if isinstance(metadata, dict) else {}
    official = metadata.get("fonbet_official_result") if isinstance(metadata.get("fonbet_official_result"), dict) else {}
    explicit = " ".join(str(official.get(key) or metadata.get(key) or "") for key in
                         ("tour", "category", "circuit", "age_group", "tournament_type"))
    if _tour(explicit) != "unknown":
        return "fonbet_metadata"
    if external_tour and _tour("", external_tour=external_tour) != "unknown":
        return "flashscore_match_archive"
    if _tour(text) != "unknown":
        return "fonbet_competition_name"
    return "unknown"


def _external_tour_level(tour_level: str | None, gender: str | None, tournament: str | None = None) -> str:
    level = str(tour_level or "").strip().casefold()
    sex = str(gender or "").strip().casefold()
    tournament_text = str(tournament or "").casefold()
    tournament_category = _tour(tournament_text)
    if tournament_category == "junior":
        return "junior"
    if level == "atp_wta":
        return {"male": "atp", "female": "wta"}.get(
            sex, tournament_category if tournament_category in {"atp", "wta"} else "unknown"
        )
    # Flashscore's female Challenger rows represent the WTA 125 circuit.
    if level == "challenger" and sex == "female":
        return "wta"
    return level if level in {"atp", "wta", "challenger", "itf", "junior"} else "unknown"


def _discipline(event: Event, competition: str) -> str:
    metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
    official = metadata.get("fonbet_official_result") if isinstance(metadata.get("fonbet_official_result"), dict) else {}
    explicit = " ".join(str(official.get(key) or metadata.get(key) or "") for key in ("discipline", "event_type", "draw_type"))
    value = " ".join((event.participant_a_name, event.participant_b_name, competition)).casefold()
    combined = f"{value} {explicit.casefold()}"
    if any(x in combined for x in ("doubles", "double", "пары", "пара ")) or "/" in value:
        return "doubles"
    if any(x in explicit.casefold() for x in ("singles", "single", "одиноч")):
        return "singles"
    return "singles" if "/" not in value else "unknown"


def _doubles_subdiscipline(event: Event, competition: str) -> str:
    """Expose only source-explicit doubles gender/disciplines."""
    metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
    official = metadata.get("fonbet_official_result") if isinstance(metadata.get("fonbet_official_result"), dict) else {}
    facts = " ".join(str(official.get(key) or metadata.get(key) or "")
                     for key in ("discipline", "event_type", "draw_type", "gender", "category"))
    text_value = f"{competition} {facts}".casefold()
    if any(token in text_value for token in ("mixed", "смешан")):
        return "mixed_doubles"
    if any(token in text_value for token in ("women", "female", "wta", "жен")):
        return "womens_doubles"
    if any(token in text_value for token in ("men", "male", "atp", "муж")):
        return "mens_doubles"
    return "unknown_doubles"


def _official_winner(event: Event) -> tuple[int | None, str]:
    winner, _scores, reason = official_result(event.status, event.metadata_json)
    return winner, reason


def _official_set_scores(event: Event) -> tuple[list[tuple[int, int]] | None, str]:
    _winner, scores, reason = official_result(event.status, event.metadata_json)
    return scores, reason


def _snapshot_set_number(snapshot) -> int | None:
    """Read the active set without requiring point/server fields used by features."""
    detail = snapshot.score_detail if isinstance(snapshot.score_detail, dict) else {}
    sport = snapshot.sport_state if isinstance(snapshot.sport_state, dict) else {}
    if snapshot.current_period is not None:
        try:
            period = int(snapshot.current_period)
            if period > 0:
                return period
        except (TypeError, ValueError):
            pass
    stats = detail.get("stats") if isinstance(detail.get("stats"), dict) else sport.get("stats", {})
    lines = stats.get("set_lines") if isinstance(stats, dict) else []
    if not isinstance(lines, list):
        return None
    live = [index + 1 for index, line in enumerate(lines)
            if isinstance(line, dict) and line.get("live")]
    return live[-1] if len(live) == 1 else None


def _point_order(value) -> int | None:
    return {"0": 0, "00": 0, "15": 1, "30": 2, "40": 3, "A": 4, "AD": 4, "ADV": 4}.get(str(value).strip().upper())


def _orient(pair, external_home_is_a: bool):
    values = list(pair or [])
    if len(values) != 2:
        return None
    return values if external_home_is_a else values[::-1]


def _snapshot_state(snapshot):
    detail = snapshot.score_detail if isinstance(snapshot.score_detail, dict) else {}
    sport = snapshot.sport_state if isinstance(snapshot.sport_state, dict) else {}
    stats = detail.get("stats") if isinstance(detail.get("stats"), dict) else sport.get("stats", {})
    game = detail.get("current_game") if isinstance(detail.get("current_game"), dict) else {}
    lines = stats.get("set_lines") if isinstance(stats, dict) else []
    lines = lines if isinstance(lines, list) else []
    live_index = next((i for i in range(len(lines) - 1, -1, -1) if isinstance(lines[i], dict) and lines[i].get("live")), None)
    period = int(snapshot.current_period) if snapshot.current_period else ((live_index + 1) if live_index is not None else None)
    line = lines[live_index] if live_index is not None else {}
    try:
        games = (int(line["games_a"]), int(line["games_b"]))
    except (KeyError, TypeError, ValueError):
        return None
    is_tiebreak = bool(game.get("is_tiebreak") or sport.get("tiebreak"))
    if is_tiebreak:
        try:
            point_a, point_b = int(game.get("points_a")), int(game.get("points_b"))
        except (TypeError, ValueError):
            return None
    else:
        point_a, point_b = _point_order(game.get("points_a")), _point_order(game.get("points_b"))
    if point_a is None or point_b is None:
        return None
    server = detail.get("server") or sport.get("server")
    server = {"player_a": 0, "player_b": 1, "a": 0, "b": 1}.get(str(server).casefold())
    return period, games, (point_a, point_b), server, is_tiebreak


def _source_point_key(item: dict, external_home_is_a: bool):
    before = item.get("state_before")
    game = item.get("game_score_before", item.get("game_score"))
    if not isinstance(before, list) or not isinstance(game, list):
        return None
    is_tiebreak = bool(item.get("tie_break"))
    if is_tiebreak:
        try:
            point_values = [int(x) for x in before]
        except (TypeError, ValueError):
            return None
    else:
        point_values = [_point_order(x) for x in before]
    point = _orient(point_values, external_home_is_a)
    games = _orient(game, external_home_is_a)
    if point is None or games is None or None in point:
        return None
    server = item.get("server")
    if server not in (0, 1, None):
        return None
    if server is not None:
        server = server if external_home_is_a else 1 - server
    return item.get("set"), tuple(games), tuple(point), server, is_tiebreak


def _validated_terminal_game(item: dict, external_home_is_a: bool, official_scores: list):
    """Validate an explicitly confirmed completed game against its official set.

    Flashscore's game_score belongs to the completed game. Ordinary games
    need not equal the final set score; a completed set must agree exactly.
    """
    if (item.get("confirmed") is not True or type(item.get("winner")) is not int
            or item["winner"] not in (0, 1)
            or item.get("quality") not in {"terminal_from_game_result", "terminal_from_tiebreak_result", "confirmed_game_result"}):
        return None
    set_no, score = item.get("set"), item.get("game_score")
    if (type(set_no) is not int or not 1 <= set_no <= len(official_scores)
            or not isinstance(score, list) or len(score) != 2
            or any(type(value) is not int or value < 0 for value in score)):
        return None
    # A 10-point deciding match tie-break is a separate segment, not a normal
    # game or completed set. Until a source-versioned format explicitly maps
    # it into the set period, keep its points but exclude game/set labels.
    if max(score) >= 10:
        return None
    after = tuple(_orient(score, external_home_is_a))
    final = tuple(official_scores[set_no - 1])
    winner = item["winner"] if external_home_is_a else 1 - item["winner"]
    if any(value > limit for value, limit in zip(after, final)) or after[winner] < 1:
        return None
    completed_set = _is_finished_set(*after)
    if completed_set and (after != final or winner != (0 if after[0] > after[1] else 1)):
        return None
    before = list(after)
    before[winner] -= 1
    if _is_finished_set(*before):
        return None
    return set_no, tuple(before), winner, completed_set


def _point_events_for_alignment(items: list[dict], parser_version: str) -> list[dict]:
    """Resolve Flashscore's post-game score using its confirmed terminal marker.

    No marker means an unknown game start. Never align that post-game score
    to the next game's snapshots or fabricate a missing terminal point.
    """
    if not parser_version.startswith("flashscore-point-history-"):
        return items
    starts = defaultdict(set)
    for item in items:
        score = item.get("game_score")
        if (item.get("quality") != "terminal_from_game_result" or item.get("confirmed") is not True
                or type(item.get("winner")) is not int or item["winner"] not in (0, 1)
                or not isinstance(score, list) or len(score) != 2
                or any(type(value) is not int or value < 0 for value in score)):
            continue
        after_state = item.get("state_after")
        if not isinstance(after_state, dict) or after_state.get("game_result") != score:
            continue
        before = score.copy()
        before[item["winner"]] -= 1
        if min(before) >= 0 and not _is_finished_set(*before):
            starts[(item.get("set"), tuple(score))].add(tuple(before))
    output = []
    for item in items:
        row = dict(item)
        if not item.get("tie_break") and item.get("quality") not in {
                "terminal_from_game_result", "terminal_from_tiebreak_result"}:
            candidates = starts.get((item.get("set"), tuple(item.get("game_score") or [])), set())
            row["game_score_before"] = list(next(iter(candidates))) if len(candidates) == 1 else None
        output.append(row)
    return output


def _snapshot_game_state(snapshot):
    """Set and game score suffice to attach a game label; features are checked later."""
    detail = snapshot.score_detail if isinstance(snapshot.score_detail, dict) else {}
    sport = snapshot.sport_state if isinstance(snapshot.sport_state, dict) else {}
    stats = detail.get("stats") if isinstance(detail.get("stats"), dict) else sport.get("stats", {})
    lines = stats.get("set_lines") if isinstance(stats, dict) else None
    if not isinstance(lines, list):
        return None
    live = [(i + 1, row) for i, row in enumerate(lines) if isinstance(row, dict) and row.get("live")]
    period = _snapshot_set_number(snapshot)
    if len(live) != 1 or period != live[0][0]:
        return None
    try:
        games = tuple(int(live[0][1][key]) for key in ("games_a", "games_b"))
    except (ValueError, TypeError, KeyError):
        return None
    return (period, games) if min(games) >= 0 else None


def _ambiguous_source_point_transition_count(items: list[dict], external_home_is_a: bool) -> int:
    """Count malformed or repeated source states that cannot identify one transition."""
    signatures = [_source_point_key(item, external_home_is_a) for item in items]
    counts = Counter(signatures)
    return sum(signature is None or counts[signature] > 1 for signature in signatures)


def _snapshot_matches_source_point(snapshot_state, signature):
    """Match the observed score exactly; unknown source fields remain wildcards.

    The source PBP can confirm the score transition while omitting its server
    marker. In that case the confirmed outcome remains a valid label. A known
    server must still agree with Fonbet's saved state.
    """
    if snapshot_state is None or signature is None or len(snapshot_state) != len(signature):
        return False
    return all(
        expected is None or observed == expected
        for observed, expected in zip(snapshot_state, signature)
    )


def _count_bucket():
    return {"events": 0, "valid_match_labels": 0, "point_examples": 0, "game_examples": 0, "set_examples": 0}


def _utc_result_time(event: Event):
    metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
    official = metadata.get("fonbet_official_result") or {}
    raw = official.get("available_at") or official.get("observed_at")
    value = None
    if isinstance(raw, str):
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            value = None
    # A scheduled start is not evidence of when the result became available.
    # Undated legacy results stay out of the training manifest.
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _score_token_pattern(signature: tuple[int, ...]) -> str:
    """PostgreSQL regex for exact ordered score tokens with flexible punctuation."""
    return r"^[^0-9]*" + r"[^0-9]+".join(map(str, signature)) + r"[^0-9]*$"


def _snapshot_search_lower_bound(event: Event) -> datetime | None:
    """Return the first time this Fonbet event could have produced a saved snapshot."""
    value = getattr(event, "first_seen_at", None)
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _result_score_snapshot_evidence(session, events: dict) -> tuple[dict, int]:
    """Find a persisted Fonbet state that exactly shows each final score.

    Search snapshots only when the official result has no source availability
    timestamp. A timestamped official result is already a valid conservative
    label time; scanning its older snapshot history can only move availability
    earlier and is unnecessary for the admission gate. The latest exact score
    snapshot is used for undated results. Scheduled time and mutable
    Event.finished_at are never consulted.
    """
    expected = {}
    for event_id, event in events.items():
        metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
        official = metadata.get("fonbet_official_result") or {}
        signature = score_signature(official.get("final_score"))
        if signature:
            expected[event_id] = signature

    evidence = {}
    # first_seen_at is a safe per-event lower bound: the event must have been
    # ingested before a snapshot referencing its ID could be saved. Search each
    # event through the event/time B-tree, newest first, and stop at its latest
    # exact score. This is a conservative availability timestamp and avoids
    # scanning/transferring the entire snapshot history for each event.
    requested = [
        (event_id, _score_token_pattern(signature), _snapshot_search_lower_bound(events[event_id]))
        for event_id, signature in expected.items()
        if _utc_result_time(events[event_id]) is None
    ]
    bounded = [item for item in requested if item[2] is not None]
    if bounded:
        # Keep each Timescale lateral lookup small. A single unnest of the full
        # archive made PostgreSQL hold a large historical read working set and
        # could starve normal request traffic on the 2 GiB database service.
        for start in range(0, len(bounded), SNAPSHOT_LOOKUP_BATCH_SIZE):
            batch = bounded[start:start + SNAPSHOT_LOOKUP_BATCH_SIZE]
            requested_params = {
                "event_ids": [item[0] for item in batch],
                "lower_bounds": [item[2] for item in batch],
            }
            latest_rows = session.execute(text("""
                WITH requested AS (
                    SELECT * FROM unnest(
                        CAST(:event_ids AS uuid[]),
                        CAST(:lower_bounds AS timestamptz[])
                    ) AS r(event_id, lower_bound)
                )
                SELECT requested.event_id, snapshot.id, snapshot.observed_at,
                       snapshot.status, snapshot.score
                FROM requested
                JOIN LATERAL (
                    SELECT id, observed_at, status, score
                    FROM event_state_snapshots
                    WHERE event_id = requested.event_id
                      AND observed_at >= requested.lower_bound
                    ORDER BY observed_at DESC, id DESC
                    LIMIT 1
                ) AS snapshot ON TRUE
            """), requested_params).yield_per(SNAPSHOT_LOOKUP_BATCH_SIZE)
            patterns = {event_id: pattern for event_id, pattern, _ in batch}
            for row in latest_rows:
                if row.score is None or re.fullmatch(patterns[row.event_id], str(row.score)) is None:
                    continue
                evidence[row.event_id] = {
                    "snapshot_id": str(row.id), "observed_at": row.observed_at,
                    "status": row.status, "score": row.score,
                }

        # Most finished matches have the exact official score in their latest
        # persisted snapshot. Only mismatches need the slower historical search;
        # this retains corrections/older matching evidence without scanning every
        # event's full tape in the common case.
        unresolved = [
            item for item in bounded
            if item[0] not in evidence and _utc_result_time(events[item[0]]) is None
        ]
        # When the official result already has a persisted source-availability
        # timestamp, that timestamp is a valid conservative fallback. Scanning
        # the full tape for an earlier exact score can only move availability
        # earlier; it is unnecessary for safety and expensive on old snapshots.
        for start in range(0, len(unresolved), SNAPSHOT_HISTORY_LOOKUP_BATCH_SIZE):
            batch = unresolved[start:start + SNAPSHOT_HISTORY_LOOKUP_BATCH_SIZE]
            historical_rows = session.execute(text("""
                WITH requested AS (
                    SELECT * FROM unnest(
                        CAST(:event_ids AS uuid[]),
                        CAST(:patterns AS text[]),
                        CAST(:lower_bounds AS timestamptz[])
                    ) AS r(event_id, score_pattern, lower_bound)
                )
                SELECT requested.event_id, snapshot.id, snapshot.observed_at,
                       snapshot.status, snapshot.score
                FROM requested
                JOIN LATERAL (
                    SELECT id, observed_at, status, score
                    FROM event_state_snapshots
                    WHERE event_id = requested.event_id
                      AND observed_at >= requested.lower_bound
                      AND score ~ requested.score_pattern
                    ORDER BY observed_at DESC, id DESC
                    LIMIT 1
                ) AS snapshot ON TRUE
            """), {
                "event_ids": [item[0] for item in batch],
                "patterns": [item[1] for item in batch],
                "lower_bounds": [item[2] for item in batch],
            }).yield_per(SNAPSHOT_HISTORY_LOOKUP_BATCH_SIZE)
            for row in historical_rows:
                evidence[row.event_id] = {
                    "snapshot_id": str(row.id), "observed_at": row.observed_at,
                    "status": row.status, "score": row.score,
                }

    # first_seen_at is non-null for normally ingested events. Retain a correct
    # no-bound fallback for legacy rows that predate this field's guarantee.
    for event_id, pattern, lower_bound in requested:
        if lower_bound is not None or event_id in evidence:
            continue
        row = session.query(
            EventStateSnapshot.id, EventStateSnapshot.event_id, EventStateSnapshot.observed_at,
            EventStateSnapshot.status, EventStateSnapshot.score,
        ).filter(
            EventStateSnapshot.event_id == event_id,
            EventStateSnapshot.score.op("~")(pattern),
        ).order_by(EventStateSnapshot.observed_at, EventStateSnapshot.id).limit(1).first()
        if row is not None:
            evidence[event_id] = {
                "snapshot_id": str(row.id), "observed_at": row.observed_at,
                "status": row.status, "score": row.score,
            }
    return evidence, len(evidence)


def _resolve_result_availability(metadata_time, snapshot_evidence):
    """Use the earliest actual result observation, never a scheduled/finished time."""
    observed_at = snapshot_evidence.get("observed_at") if snapshot_evidence else None
    if observed_at is not None and (metadata_time is None or observed_at < metadata_time):
        return observed_at, "fonbet_exact_final_score_snapshot"
    if metadata_time is not None:
        return metadata_time, "fonbet_official_result_metadata"
    return None, None


def _trusted_link_orientation(evidence):
    """Reject legacy side assignments inferred from URLs or names."""
    evidence = evidence if isinstance(evidence, dict) else {}
    value = evidence.get("external_home_is_fonbet_a")
    if (evidence.get("method") == "automatic_exact_confirmed_player_ids"
            and (evidence.get("side_orientation_source") != "flashscore_scoreboard_dom_confirmed_profile_ids"
                 or not evidence.get("raw_hash") or not evidence.get("raw_object_key"))):
        return None
    if evidence.get("side_orientation_source") == "flashscore_match_observation_names":
        return None
    return value if isinstance(value, bool) else None


def _window_summary(event_ids, event_times, labels_by_event, required):
    """Count only whole matches that actually have a target label in this split."""
    ids = set(event_ids)
    labelled_ids = {event_id for event_id in ids if labels_by_event.get(event_id)}
    class_a = sum(0 in labels_by_event.get(event_id, set()) for event_id in labelled_ids)
    class_b = sum(1 in labels_by_event.get(event_id, set()) for event_id in labelled_ids)
    return {"matches": len(labelled_ids), "candidate_matches": len(ids),
            "class_a": class_a, "class_b": class_b,
            "both_classes": class_a > 0 and class_b > 0,
            "meets_match_minimum": len(labelled_ids) >= required}


def _temporal_precedence(train_ids, validation_ids, target_temporal):
    """Require every selected train outcome to predate validation forecasts."""
    train_labels = [target_temporal.get(event_id, {}).get("label_available_at") for event_id in train_ids]
    validation_forecasts = [target_temporal.get(event_id, {}).get("forecast_at") for event_id in validation_ids]
    missing_train = sum(value is None for value in train_labels)
    missing_validation = sum(value is None for value in validation_forecasts)
    valid_train = [value for value in train_labels if value is not None]
    valid_validation = [value for value in validation_forecasts if value is not None]
    latest_train_label = max(valid_train) if valid_train else None
    earliest_validation_forecast = min(valid_validation) if valid_validation else None
    passed = (missing_train == 0 and missing_validation == 0 and latest_train_label is not None
              and earliest_validation_forecast is not None
              and latest_train_label <= earliest_validation_forecast)
    return {"passed": bool(passed), "missing_train_label_times": missing_train,
            "missing_validation_forecast_times": missing_validation,
            "latest_train_label_available_at": latest_train_label.isoformat() if latest_train_label else None,
            "earliest_validation_forecast_at": earliest_validation_forecast.isoformat() if earliest_validation_forecast else None}


def _causal_training_membership(candidate_train_ids, validation_ids, labels, target_temporal):
    """Purge labelled train matches whose outcomes were unknown at validation start."""
    validation_times = [target_temporal.get(event_id, {}).get("forecast_at")
                        for event_id in validation_ids]
    missing_validation = sum(value is None for value in validation_times)
    cutoff = min((value for value in validation_times if value is not None), default=None)
    # A missing validation time makes the fold boundary ambiguous; fail closed
    # instead of choosing a later forecast and accidentally admitting labels.
    if missing_validation:
        cutoff = None
    labelled_candidates = [event_id for event_id in candidate_train_ids if labels.get(event_id)]
    missing_label_time = [event_id for event_id in labelled_candidates
                          if target_temporal.get(event_id, {}).get("label_available_at") is None]
    future_label_time = [event_id for event_id in labelled_candidates
                         if cutoff is not None
                         and target_temporal.get(event_id, {}).get("label_available_at") is not None
                         and target_temporal[event_id]["label_available_at"] > cutoff]
    if cutoff is None:
        eligible = []
    else:
        eligible = [event_id for event_id in labelled_candidates
                    if target_temporal.get(event_id, {}).get("label_available_at") is not None
                    and target_temporal[event_id]["label_available_at"] <= cutoff]
    return eligible, {
        "cutoff": cutoff.isoformat() if cutoff else None,
        "candidate_labelled_matches": len(labelled_candidates),
        "eligible_matches": len(eligible),
        "purged_future_label_matches": len(future_label_time),
        "purged_unknown_label_time_matches": len(missing_label_time),
        "purged_no_validation_boundary_matches": len(labelled_candidates) if cutoff is None else 0,
        "missing_validation_forecast_times": missing_validation,
    }


def _walk_forward_admission(target_rows, event_times, target_temporal=None):
    """Build one immutable whole-match split shared by all target masks."""
    # Split ordering uses forecast time, independent of when the confirmed
    # outcome was received. Each training fold separately purges labels whose
    # own source availability is unknown or later than its validation boundary.
    ordered = sorted(event_times, key=lambda event_id: (event_times[event_id], str(event_id)))
    digest_input = "\n".join(f"{event_id}|{event_times[event_id].isoformat()}" for event_id in ordered)
    digest = hashlib.sha256(digest_input.encode()).hexdigest()
    manifest_path = os.getenv("TENNIS_TRAINING_MANIFEST_PATH") or f"docs/research/tennis_training_manifest_{digest[:12]}.json"
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        if manifest.get("dataset_digest") != digest:
            raise RuntimeError("manifest path already contains a different dataset digest")
    else:
        holdout_start = int(len(ordered) * 0.8)
        development, holdout = ordered[:holdout_start], ordered[holdout_start:]
        initial_end = int(len(development) * 0.6)
        validation = development[initial_end:]
        edges = [round(i * len(validation) / 4) for i in range(5)]
        folds = [{"fold": i + 1,
                  "train_event_ids": [str(x) for x in development[:initial_end + edges[i]]],
                  "validation_event_ids": [str(x) for x in validation[edges[i]:edges[i + 1]]]} for i in range(4)]
        manifest = {"manifest_version": "tennis_shared_walk_forward_v3",
                    "created_at": datetime.now(timezone.utc).isoformat(), "dataset_digest": digest,
                    "ordered_event_ids": [str(x) for x in ordered],
                    "event_times": {str(x): event_times[x].isoformat() for x in ordered},
                    "development_event_ids": [str(x) for x in development],
                    "holdout_event_ids": [str(x) for x in holdout], "folds": folds,
                    "holdout_quality_metrics_opened": False}
    manifest_changed = (
        manifest.get("manifest_version") != "tennis_shared_walk_forward_v3"
        or manifest.get("training_cutoff_policy")
        != "only_source_timed_labels_available_by_first_validation_forecast_enter_training"
    )
    manifest["manifest_version"] = "tennis_shared_walk_forward_v3"
    manifest["training_cutoff_policy"] = "only_source_timed_labels_available_by_first_validation_forecast_enter_training"
    manifest["split_order_policy"] = "first_forecast_snapshot_time; this does not assert result availability"

    def ids(values):
        return [uuid.UUID(value) for value in values if uuid.UUID(value) in event_times]

    results = {}
    target_temporal = target_temporal or {}
    for target, rows in target_rows.items():
        labels = defaultdict(set)
        for row in rows:
            if row and row[0] in event_times and row[-1] in (0, 1):
                labels[row[0]].add(row[-1])
        folds = []
        for fold in manifest["folds"]:
            train_ids, validation_ids = ids(fold["train_event_ids"]), ids(fold["validation_event_ids"])
            temporal_validation_ids = [event_id for event_id in validation_ids if event_id in labels]
            eligible_train_ids, training_filter = _causal_training_membership(
                train_ids, temporal_validation_ids, labels, target_temporal.get(target, {}))
            target_train_membership = fold.setdefault("target_train_event_ids", {})
            eligible_ids = [str(event_id) for event_id in eligible_train_ids]
            if target_train_membership.get(target) != eligible_ids:
                target_train_membership[target] = eligible_ids
                manifest_changed = True
            temporal = _temporal_precedence(eligible_train_ids, temporal_validation_ids,
                                            target_temporal.get(target, {}))
            folds.append({"fold": fold["fold"],
                "train": _window_summary(eligible_train_ids, event_times, labels, 500),
                "train_candidates": _window_summary(train_ids, event_times, labels, 500),
                "training_filter": training_filter,
                "validation": _window_summary(validation_ids, event_times, labels, 100),
                "temporal_precedence": temporal})
        holdout_summary = _window_summary(ids(manifest["holdout_event_ids"]), event_times, labels, 200)
        results[target] = {"matches_with_labels_on_manifest_timeline": len(labels),
            "manifest_matches": len(manifest["ordered_event_ids"]),
            "development_matches": len(manifest["development_event_ids"]),
            "holdout": holdout_summary, "folds": folds,
            "folds_meet_minimums": bool(folds) and all(
                f["train"]["meets_match_minimum"] and f["validation"]["meets_match_minimum"]
                and f["train"]["both_classes"] and f["validation"]["both_classes"]
                and f["temporal_precedence"]["passed"] for f in folds),
            "holdout_meets_minimums": holdout_summary["meets_match_minimum"] and holdout_summary["both_classes"]}
    if manifest_changed:
        os.makedirs(os.path.dirname(manifest_path) or ".", exist_ok=True)
        with open(manifest_path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        os.replace(manifest_path + ".tmp", manifest_path)
    return results, {"path": manifest_path, "dataset_digest": digest,
        "matches": len(manifest["ordered_event_ids"]), "development_matches": len(manifest["development_event_ids"]),
        "holdout_matches": len(manifest["holdout_event_ids"]), "holdout_quality_metrics_opened": False}


def _training_gate_status(*, base_coverage_ok: bool, windows_ok: bool,
                          temporal_ok: bool, point_in_time_ok: bool,
                          manifest_result_times_certified: bool) -> tuple[str, list[str]]:
    """Fail closed unless every independent training admission check passes."""
    reasons = []
    if not base_coverage_ok:
        reasons.append("base_target_coverage_or_both_classes_missing")
    if not windows_ok:
        reasons.append("walk_forward_or_holdout_minimums_not_met")
    if not temporal_ok:
        reasons.append("training_labels_not_available_before_validation_forecasts")
    if not point_in_time_ok:
        reasons.append("odds_free_feature_availability_not_certified")
    if not manifest_result_times_certified:
        reasons.append("manifest_result_availability_not_certified")
    return ("eligible_for_training_review" if not reasons else "blocked", reasons)


def build_report(*, snapshot_index_path: str | None = None,
                 result_evidence_index_path: str | None = None,
                 preloaded_snapshot_rows=None,
                 filter_unusable_examples: bool = False,
                 defer_admission_windows: bool = False,
                 training_examples_output_dir: str = "docs/research",
                 snapshot_coverage_output_dir: str | None = None,
                 audit_cutoff: datetime | None = None,
                 discipline_scope: str = "singles",
                 event_batch_size: int = 100,
                 snapshot_page_size: int = 500,
                 query_timeout_seconds: int = 30) -> dict:
    now = audit_cutoff or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("audit_cutoff_requires_timezone")
    if not 1 <= event_batch_size <= 100:
        raise ValueError("event_batch_size_out_of_bounds")
    if not 1 <= snapshot_page_size <= 5000:
        raise ValueError("snapshot_page_size_out_of_bounds")
    if not 1 <= query_timeout_seconds <= 30:
        raise ValueError("query_timeout_seconds_out_of_bounds")
    if discipline_scope not in {"singles", "doubles"}:
        raise ValueError("discipline_scope_must_be_singles_or_doubles")
    report = {
        "generated_at": now.isoformat(),
        "source_scope": f"numeric Fonbet IDs; {discipline_scope}; each feature and label gated by its source availability time",
        "dataset_contract": "tennis_odds_free_v2" if discipline_scope == "singles" else "tennis_doubles_odds_free_v1",
        "targets": {name: {"eligible_matches": 0, "labelled_matches": 0, "parsed_labels": 0,
            "training_examples": 0, "class_a": 0, "class_b": 0,
            "observed_examples": 0, "retrospective_examples": 0}
            for name in ("point", "game", "set", "match")},
        "by_tour": {}, "by_discipline": {}, "by_competition": {},
        "tour_classification_sources": {},
        "result_time_evidence": {"snapshot_rows_returned": 0, "exact_terminal_score_snapshots": 0,
            "snapshots_preceding_metadata_time": 0, "sources": {}, "unknown_valid_results": 0},
        "point_history": {"documents_total": 0, "documents_linked": 0, "source_matches_total": 0,
            "current_links": 0, "linked_fonbet_events": 0, "parsed_versions_total": 0,
            "parsed_versions_linked": 0, "confirmed_outcomes_total": 0,
            "availability_unknown_documents": 0, "availability_unknown_versions": 0,
            "global_history_scan_status": "not_scanned"},
        "feature_availability": {"contract_version": PIT_FEATURE_CONTRACT_VERSION,
            "checked_examples": 0, "certified_examples": 0, "uncertified_examples": 0,
            "fully_source_observed_examples": 0, "contract_defaulted_examples": 0,
            "point_in_time_definition": (
                "certified means every source-backed feature is available by the snapshot; "
                "a documented contract prior or explicit unknown value is allowed only when "
                "its missingness/sample-weight representation is part of the feature contract"),
            "source_groups": {}, "resolution_counts": {}, "exclusions": {}},
        "snapshot_source_coverage": {"checked_snapshots": 0, "skipped_outside_source_scope": 0,
            "source_groups": {}, "resolution_counts": {}, "exclusions": {}},
        "exclusions": {},
        "training_gate": {"status": "blocked", "required_per_target": {
            "initial_train_matches": 500, "each_validation_window_matches": 100, "holdout_matches": 200,
            "both_classes": True, "point_in_time_clean": True}, "windows_evaluated": False,
            "point_in_time_certified": False, "reasons": [],
            "chronology_policy": "official_result_source_time_or_exact_previously_observed_final_score_snapshot"},
        "audit_format_version": 6,
        "alignment_policy": "confirmed_pre_game_score_v1",
        "history_link_schema": "provider_scoped_links_v2",
        "target_diagnostics": {
            name: {"unique_matches_with_parsed_labels": 0,
                  "unique_matches_with_snapshot_aligned_labels": 0,
                  "unique_matches_with_feature_certified_labels": 0,
                  "unique_matches_with_causal_train_labels_by_fold": [],
                  "stage_exclusions": {}, "secondary_exclusions": {}}
            for name in ("point", "game", "set", "match")
        },
    }
    exclusions = Counter()
    cohort = _read_saved_snapshot_cohort(snapshot_index_path) if snapshot_index_path else None
    if not cohort:
        report["audit_scope"] = {"mode": "full_database", "full_database_census": True,
                                 "status": "running", "as_of": now.isoformat()}
    with SessionLocal() as session:
        session.execute(text(f"SET LOCAL statement_timeout = '{query_timeout_seconds}s'"))
        if preloaded_snapshot_rows is not None:
            if cohort is None:
                raise ValueError("preloaded_rows_require_frozen_snapshot_index")
            expected_keys = {(key[0], key[1], event_id) for key,event_id in cohort['keys'].items()}
            actual_keys = {(r.id,r.observed_at,r.event_id) for r in preloaded_snapshot_rows}
            if len(preloaded_snapshot_rows) != len(actual_keys) or actual_keys != expected_keys:
                raise ValueError("preloaded_snapshot_keys_do_not_match_frozen_input")
            indexed_snapshots = preloaded_snapshot_rows
        else:
            indexed_snapshots = _load_saved_snapshot_cohort(session, cohort) if cohort else None
        result_witnesses = []
        if result_evidence_index_path:
            if not cohort:
                raise ValueError("result_evidence_index_requires_snapshot_cohort")
            evidence_index = _read_saved_snapshot_cohort(result_evidence_index_path)
            if not evidence_index["event_ids"].issubset(cohort["event_ids"]):
                raise ValueError("result_evidence_event_outside_snapshot_cohort")
            result_witnesses = _load_result_snapshot_keys(session, evidence_index)
            if len(result_witnesses) != len(evidence_index["keys"]):
                raise ValueError("result_evidence_snapshot_missing")
            report["result_time_evidence"]["additional_index"] = {
                "path": result_evidence_index_path, "sha256": evidence_index["sha256"],
                "loaded_snapshots": len(result_witnesses), "feature_snapshots_added": 0,
            }
        if cohort:
            report["audit_scope"] = {
                "mode": "saved_snapshot_cohort_revalidated_from_database",
                "index_path": cohort["path"], "index_sha256": cohort["sha256"],
                "indexed_examples": cohort["examples"], "indexed_events": len(cohort["event_ids"]),
                "indexed_snapshot_keys": len(cohort["keys"]),
                "loaded_snapshots": len(indexed_snapshots),
                "missing_snapshots": len(cohort["keys"]) - len(indexed_snapshots),
                "full_database_census": False,
            }
        # This classification is retrospective reporting metadata only; it is
        # never added to the model's point-in-time feature vector.
        link_rows = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.fonbet_event_id.is_not(None),
            TennisPointMatchLink.fonbet_event_id.in_(cohort["event_ids"]) if cohort else True,
            TennisPointMatchLink.available_at <= now,
        ).order_by(TennisPointMatchLink.available_at, TennisPointMatchLink.id).yield_per(1000)
        latest_links = {}
        for link in link_rows:
            latest_links[(link.fonbet_event_id, link.source_match_id)] = link
        source_ids_by_event = defaultdict(set)
        for (event_id, source_id), link in latest_links.items():
            if link.status in ("matched", "manual"):
                source_ids_by_event[event_id].add(source_id)
        category_source_ids = {source_id for source_ids in source_ids_by_event.values()
                               for source_id in source_ids}
        external_by_source_id = {}
        if category_source_ids:
            for match in session.query(ExtTennisMatch.source_match_id, ExtTennisMatch.tour_level,
                                       ExtTennisMatch.gender, ExtTennisMatch.tournament).filter(
                ExtTennisMatch.source == "flashscore",
                ExtTennisMatch.source_match_id.in_(category_source_ids),
            ).yield_per(1000):
                external_by_source_id[match.source_match_id] = match
        external_tour_by_event = {}
        for event_id, source_ids in source_ids_by_event.items():
            # More than one confirmed source match is not an identity-safe
            # basis for classification, even if their circuit labels happen
            # to agree.
            if len(source_ids) != 1:
                continue
            source_id = next(iter(source_ids))
            match = external_by_source_id.get(source_id)
            level = _external_tour_level(match.tour_level, match.gender, match.tournament) if match else "unknown"
            if level != "unknown":
                external_tour_by_event[event_id] = level

        events_query = session.query(Event).filter(Event.sport_code == "tennis", Event.source == "fonbet")
        if cohort:
            events_query = events_query.filter(Event.id.in_(cohort["event_ids"]))
        events = events_query.yield_per(1000)
        eligible = {}
        out_of_scope_diagnostics = []
        result_labels = {}
        result_times = {}
        result_time_sources = {}
        result_snapshot_evidence = {}
        groups = {}
        for event in events:
            if not str(event.source_event_id).isdigit():
                exclusions["non_numeric_fonbet_id"] += 1
                out_of_scope_diagnostics.append({'fonbet_event_id':str(event.id),
                    'fonbet_source_event_id':str(event.source_event_id),'reason':'non_numeric_fonbet_id'})
                continue
            metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
            official = metadata.get("fonbet_official_result") or {}
            league = session.get(League, event.league_id) if event.league_id else None
            competition = str(official.get("tournament") or metadata.get("raw_tournament") or (league.name if league else "unknown"))
            discipline = _discipline(event, competition)
            external_tour = external_tour_by_event.get(event.id)
            tour = _tour(competition, metadata, external_tour)
            tour_source = _tour_source(competition, metadata, external_tour)
            if discipline != discipline_scope:
                excluded_scope = "doubles" if discipline_scope == "singles" else "singles"
                exclusions[excluded_scope] += 1
                out_of_scope_diagnostics.append({'fonbet_event_id':str(event.id),
                    'fonbet_source_event_id':str(event.source_event_id),'reason':excluded_scope})
                continue
            eligible[event.id] = event
            if discipline_scope == "doubles":
                discipline = _doubles_subdiscipline(event, competition)
            else:
                discipline = "singles"
            result_times[event.id] = _utc_result_time(event)
            result_time_sources[event.id] = "fonbet_official_result_metadata" if result_times[event.id] else None
            groups[event.id] = (tour, discipline, competition)
            report["tour_classification_sources"][tour_source] = (
                report["tour_classification_sources"].get(tour_source, 0) + 1
            )
            for key, name in (("by_tour", tour), ("by_discipline", discipline), ("by_competition", competition)):
                bucket = report[key].setdefault(name, _count_bucket())
                bucket["events"] += 1
            report["targets"]["match"]["eligible_matches"] += 1
            winner, reason = _official_winner(event)
            result_labels[event.id] = winner
            if winner is None:
                exclusions[reason] += 1
                continue
            report["targets"]["match"]["labelled_matches"] += 1
            report["targets"]["match"]["parsed_labels"] += 1
            report["targets"]["match"]["class_a" if winner == 0 else "class_b"] += 1
            for key, name in (("by_tour", tour), ("by_discipline", discipline), ("by_competition", competition)):
                report[key][name]["valid_match_labels"] += 1

        result_events = {event_id: event for event_id, event in eligible.items() if result_labels[event_id] is not None}
        if indexed_snapshots is not None:
            snapshot_evidence = _indexed_result_snapshot_evidence(
                result_events, chain(indexed_snapshots, result_witnesses), cutoff=now)
            snapshot_rows_returned = len(snapshot_evidence)
        else:
            snapshot_evidence, snapshot_rows_returned = _result_score_snapshot_evidence(session, result_events)
        report["result_time_evidence"]["snapshot_rows_returned"] = snapshot_rows_returned
        for event_id, evidence in snapshot_evidence.items():
            report["result_time_evidence"]["exact_terminal_score_snapshots"] += 1
            result_snapshot_evidence[event_id] = evidence
            metadata_time = result_times.get(event_id)
            result_times[event_id], result_time_sources[event_id] = _resolve_result_availability(
                metadata_time, evidence,
            )
            if metadata_time is not None and result_time_sources[event_id] == "fonbet_exact_final_score_snapshot":
                report["result_time_evidence"]["snapshots_preceding_metadata_time"] += 1
        report["result_time_evidence"]["unknown_valid_results"] = sum(
            result_labels[event_id] is not None and result_times.get(event_id) is None
            for event_id in eligible
        )
        report["result_time_evidence"]["sources"] = dict(Counter(
            result_time_sources[event_id] or "unavailable"
            for event_id in eligible if result_labels[event_id] is not None
        ))
        late_result_ids = {event_id for event_id, label_time in result_times.items()
                           if result_labels.get(event_id) in (0, 1)
                           and label_time is not None and label_time > now}
        report["result_time_evidence"]["labels_after_audit_cutoff"] = len(late_result_ids)
        exclusions["result_after_audit_cutoff"] += len(late_result_ids)
        for event_id in late_result_ids:
            result_labels[event_id] = None

        # Historical serving logs currently retain the source match and name,
        # but not the provider player ID / identity decision used at ingest.
        # Never join them back by today's display name. Only participant-keyed
        # immutable metric snapshots below can provide serve features here.
        participant_ids = {pid for event in eligible.values()
                           for pid in (event.participant_a_id, event.participant_b_id) if pid}
        # Limit immutable identity/metric history to participants that occur
        # in this audit. The former unfiltered `.all()` calls loaded every
        # historical profile and metric into RAM before examining a snapshot.
        event_participant_ids = {pid for event in eligible.values()
                                 for pid in (event.participant_a_id, event.participant_b_id) if pid}
        initial_identity_rows = list(session.query(TennisPlayerIdentityLink).filter(
            TennisPlayerIdentityLink.participant_id.in_(event_participant_ids) if event_participant_ids else False,
            TennisPlayerIdentityLink.source == "flashscore",
            TennisPlayerIdentityLink.available_at <= now,
        ).order_by(TennisPlayerIdentityLink.available_at, TennisPlayerIdentityLink.id).yield_per(1000))
        relevant_source_ids = {row.source_player_id for row in initial_identity_rows if row.source_player_id}
        related_identity_rows = session.query(TennisPlayerIdentityLink).filter(
            TennisPlayerIdentityLink.source == "flashscore",
            TennisPlayerIdentityLink.available_at <= now,
            TennisPlayerIdentityLink.source_player_id.in_(relevant_source_ids) if relevant_source_ids else False,
        ).order_by(TennisPlayerIdentityLink.available_at, TennisPlayerIdentityLink.id).yield_per(1000)
        identity_versions_by_participant = defaultdict(list)
        seen_identity_ids = set()
        for row in chain(initial_identity_rows, related_identity_rows):
            if row.id in seen_identity_ids:
                continue
            seen_identity_ids.add(row.id)
            identity_versions_by_participant[row.participant_id].append(row)
        relevant_participant_ids = event_participant_ids | set(identity_versions_by_participant)
        profile_rows = session.query(TennisPlayerProfile).filter(
            TennisPlayerProfile.participant_id.in_(relevant_participant_ids)
            if relevant_participant_ids else False
        ).yield_per(1000)
        profiles_by_participant = {row.participant_id: row for row in profile_rows}
        metric_rows = session.query(TennisPlayerMetricSnapshot).order_by(
            TennisPlayerMetricSnapshot.available_at
        ).filter(
            TennisPlayerMetricSnapshot.participant_id.in_(relevant_participant_ids)
            if relevant_participant_ids else False
        ).yield_per(1000)
        metrics_by_participant = defaultdict(list)
        for metric in metric_rows:
            metrics_by_participant[metric.participant_id].append(metric)
        feature_availability_by_event_snapshot = {}
        feature_values_by_event_snapshot = {}
        snapshot_rows_by_id = {}
        attribute_observations_by_event = defaultdict(list)
        for start in range(0, len(eligible), 200):
            event_batch = list(eligible)[start:start + 200]
            for observation in session.query(TennisEventAttributeObservation).filter(
                TennisEventAttributeObservation.event_id.in_(event_batch),
                TennisEventAttributeObservation.attribute_name == "best_of",
                TennisEventAttributeObservation.available_at <= now,
            ).order_by(TennisEventAttributeObservation.event_id,
                       TennisEventAttributeObservation.attribute_name,
                       TennisEventAttributeObservation.available_at,
                       TennisEventAttributeObservation.id).yield_per(1000):
                attribute_observations_by_event[observation.event_id].append(observation)

        def feature_availability(event_id, snapshot_id, snapshot_at):
            key = (event_id, snapshot_id)
            if key in feature_availability_by_event_snapshot:
                return feature_availability_by_event_snapshot[key]
            event = eligible[event_id]
            player_times = []
            serve_times = {"a": [], "b": []}
            metric_values_by_side = {}
            serve_values_by_side = {}
            player_resolution = {}
            serve_resolution = {}
            player_evidence = {}
            serve_evidence = {"a": None, "b": None}
            event = eligible[event_id]
            for side, participant_id in (("a", event.participant_a_id), ("b", event.participant_b_id)):
                profile = profiles_by_participant.get(participant_id)
                # Identity links are append-only decisions. The first row is
                # often an earlier candidate/rejection; resolve the last
                # decision that was actually available at this snapshot.
                latest_link = _latest_identity_link_as_of(
                    identity_versions_by_participant.get(participant_id, []), snapshot_at)
                current_identities = (profile.source_identities or {}) if profile else {}
                confirmed_id = latest_link.source_player_id if latest_link and latest_link.status == "confirmed" else None
                identity_valid = bool(profile)
                if confirmed_id and current_identities.get("flashscore") not in (None, confirmed_id):
                    identity_valid = False
                if not confirmed_id and (current_identities.get("flashscore")
                        or current_identities.get("flashscore_candidates") or profile.profile_url if profile else False):
                    identity_valid = False
                metric_ids = {participant_id} if participant_id else set()
                if identity_valid and confirmed_id:
                    metric_ids = set()
                    for candidate_id, versions in identity_versions_by_participant.items():
                        candidate_link = _latest_identity_link_as_of(versions, snapshot_at)
                        if candidate_link and candidate_link.status == "confirmed" and candidate_link.source_player_id == confirmed_id:
                            metric_ids.add(candidate_id)
                available = [row for metric_id in metric_ids
                             for row in metrics_by_participant.get(metric_id, [])
                             if row.available_at <= snapshot_at] if identity_valid else []
                metric_available_at = max((row.available_at for row in available), default=None)
                metric_values = max(available, key=lambda row: (row.available_at, row.metrics_version)).metrics_json if available else None
                if metric_available_at is None and identity_valid:
                    # load_player_metric has a compatibility fallback to the
                    # materialized current profile when no immutable snapshot
                    # was yet available. Its own metrics_available_at remains
                    # the causal bound and must be represented by the audit.
                    eligible_profiles = [profiles_by_participant[metric_id]
                        for metric_id in metric_ids
                        if metric_id in profiles_by_participant
                        and profiles_by_participant[metric_id].metrics_available_at is not None
                        and profiles_by_participant[metric_id].metrics_available_at <= snapshot_at
                        and profiles_by_participant[metric_id].metrics_json is not None]
                    if eligible_profiles:
                        owner = max(eligible_profiles, key=lambda row: row.metrics_available_at)
                        metric_available_at, metric_values = owner.metrics_available_at, owner.metrics_json
                metric_values_by_side[side] = metric_values
                if metric_available_at is not None:
                    player_times.append(metric_available_at)
                    player_resolution[side] = "observed_snapshot" if available else "observed_profile_cache"
                    selected_metric = max(available, key=lambda row: (row.available_at, row.metrics_version)) if available else None
                    player_evidence[side] = {
                        "source": "tennis_player_metric_snapshot" if selected_metric else "tennis_player_profile",
                        "snapshot_id": str(selected_metric.id) if selected_metric else str(participant_id),
                        "available_at": metric_available_at.isoformat(),
                        "history_digest": selected_metric.history_digest if selected_metric else None,
                        "identity_link_id": str(latest_link.id) if latest_link and latest_link.status == "confirmed" else None,
                    }
                else:
                    # Missing metrics are encoded by the model as the fixed Elo /
                    # form prior and a missingness flag. The prior is knowable at
                    # this as_of, so retain it as a contract default explicitly.
                    player_resolution[side] = "contract_default"
                metric_serve = ((metric_values or {}).get("serve") or {})
                if float(metric_serve.get("points") or 0) > 0 and metric_available_at is not None:
                    serve_times[side].append(metric_available_at)
                    serve_resolution[side] = "metric_snapshot"
                    serve_values_by_side[side] = (metric_serve.get("points"), metric_serve.get("points_won"))
                    serve_evidence[side] = player_evidence.get(side)
                else:
                    # Legacy serve logs do not retain a provider player ID and
                    # time of identity confirmation. Do not join them by name.
                    serve_resolution[side] = "identity_provenance_missing"
            # Surface is only consumed by inference with its own explicit
            # availability timestamp. Historical rows without one use unknown.
            event_attributes = resolve_event_attribute_observations(
                attribute_observations_by_event.get(event_id, []), snapshot_at)
            format_observation = event_attributes.get("best_of")
            format_has_late_observation = any(
                observation.available_at > snapshot_at
                for observation in attribute_observations_by_event.get(event_id, [])
            )
            surface_observation = event_attributes.get("surface")
            format_at = format_observation.available_at if format_observation else None
            surface_at = surface_observation.available_at if surface_observation else None
            try:
                explicit_best_of = int(format_observation.value_json.get("value")) if format_observation else None
            except (TypeError, ValueError, OverflowError):
                explicit_best_of = None
            format_known = bool(format_at and explicit_best_of in {3, 5})
            sources = {
                "player_metrics": max(player_times).isoformat() if player_times else None,
                "serve_metrics_a": max(serve_times["a"]).isoformat() if serve_times["a"] else None,
                "serve_metrics_b": max(serve_times["b"]).isoformat() if serve_times["b"] else None,
                "event_state": snapshot_at.isoformat(),
                **({"event_format": format_at.isoformat()} if format_known else {}),
            }
            contract_defaults = set()
            if "contract_default" in player_resolution.values():
                contract_defaults.add("player_metrics")
            if serve_resolution.get("a") == "contract_default":
                contract_defaults.add("serve_metrics_a")
            if serve_resolution.get("b") == "contract_default":
                contract_defaults.add("serve_metrics_b")
            source_evidence = {
                           "player_metrics": player_evidence,
                           "serve_metrics_a": serve_evidence.get("a"),
                           "serve_metrics_b": serve_evidence.get("b"),
                           "event_state": _event_state_source_evidence(
                               snapshot_rows_by_id.get(snapshot_id), snapshot_id, snapshot_at),
                           "event_format": ({"source": format_observation.source, "snapshot_id": str(format_observation.id),
                                            "available_at": format_at.isoformat(),
                                            "content_hash": format_observation.source_content_hash,
                                            "evidence_hash": format_observation.evidence_hash,
                                            "raw_object_key": format_observation.raw_object_key,
                                            "identity_available_at": format_observation.identity_available_at.isoformat() if format_observation.identity_available_at else None}
                                           if format_observation else None),
                       }
            snapshot_row = snapshot_rows_by_id.get(snapshot_id)
            materialized = (_materialize_odds_free_feature_values(
                event, snapshot_row, snapshot_at, metric_values_by_side, serve_values_by_side, event_attributes
            ) if snapshot_row is not None else None)
            sources, source_evidence, materialized, player_fallback = resolve_unknown_player_history(
                snapshot_at, sources, source_evidence, materialized)
            contract_defaults.discard("player_metrics")
            sources, source_evidence, materialized, serve_fallback = resolve_unknown_serve_history(
                snapshot_at, sources, source_evidence, materialized)
            for side in serve_fallback:
                contract_defaults.discard("serve_metrics_" + side)
                serve_resolution[side] = "unknown_serve_history_v1"
            for side in player_fallback:
                player_resolution[side] = "unknown_player_history_v1"
            required_groups = tuple(group for group in PIT_FEATURE_SOURCE_GROUPS
                                    if group != "event_format" or format_known)
            validation = _validate_feature_availability(snapshot_at, sources, contract_defaults, required_groups)
            strict_validation = validate_feature_source_evidence(
                snapshot_at, sources, source_evidence, required_groups)
            validation.update(strict_validation)
            validation["contract_defaulted_sources"] = sorted(contract_defaults)
            details = {**validation, "source_available_at": sources,
                       "feature_missing_fields": _missing_feature_values(materialized),
                       "player_history_fallback": player_fallback,
                       "serve_history_fallback": serve_fallback,
                       "source_evidence": source_evidence,
                       "optional_unknown_groups": [] if format_known else ["event_format"],
                       "optional_unknown_reasons": ({} if format_known else {
                           "event_format": "source_after_cutoff" if format_has_late_observation
                           else "no_explicit_source_observation"}),
                       "source_resolution": {
                           "player_metrics": player_resolution,
                           "serve_metrics": serve_resolution,
                           "event_format": "observed" if format_known else (
                               "late_unknown_allowed" if format_has_late_observation else "unknown_allowed"),
                           "event_state": "observed_snapshot",
                       }}
            feature_availability_by_event_snapshot[key] = details
            snapshot_row = snapshot_rows_by_id.get(snapshot_id)
            if snapshot_row is not None:
                feature_values_by_event_snapshot[key] = materialized
            return details

        event_link_rows = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.fonbet_event_id.in_(list(eligible)),
            TennisPointMatchLink.available_at <= now,
        ).yield_per(1000) if eligible else []
        candidate_source_ids = {link.source_match_id for link in event_link_rows}
        link_rows = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.source_match_id.in_(candidate_source_ids) if candidate_source_ids else False,
            TennisPointMatchLink.fonbet_event_id.is_not(None),
            TennisPointMatchLink.available_at <= now,
        ).order_by(TennisPointMatchLink.available_at, TennisPointMatchLink.id).yield_per(1000)
        latest_links = {}
        for link in link_rows:
            latest_links[link.source_match_id] = link
        links = [link for link in latest_links.values()
                 if link.fonbet_event_id in eligible and link.status in ("matched", "manual")]
        report["point_history"]["current_links"] = len(links)
        report["point_history"]["linked_fonbet_events"] = len({link.fonbet_event_id for link in links})
        orientation = {}
        source_ids_by_event = defaultdict(set)
        link_ids_by_event_source = {}
        link_available_at_by_event_source = {}
        source_contexts = {}
        for link in links:
            source_ids_by_event[link.fonbet_event_id].add(link.source_match_id)
            orientation[(link.fonbet_event_id, link.source_match_id)] = _trusted_link_orientation(link.evidence_json)
            link_ids_by_event_source[(link.fonbet_event_id, link.source_match_id)] = str(link.id)
            link_available_at_by_event_source[(link.fonbet_event_id, link.source_match_id)] = link.available_at.isoformat()
            source_contexts[link.source_match_id] = {'source': 'flashscore', 'source_match_id': link.source_match_id}
        source_ids = list({source_id for values in source_ids_by_event.values() for source_id in values})
        # A corpus-wide fetch of point versions is not needed to establish
        # training admission and can read >1 GiB of JSONB on production. Keep
        # the linked-match counts below exact; leave only non-gating global
        # corpus totals explicitly unavailable until a database-copy audit.
        report["point_history"].update({
            "parsed_versions_total": None,
            "source_matches_total": None,
            "availability_unknown_versions": None,
            "confirmed_outcomes_total": None,
            "global_history_scan_status": "deferred_resource_guard_copy_database_audit",
        })
        if cohort:
            report["point_history"]["documents_total"] = None
            report["point_history"]["availability_unknown_documents"] = None
            report["point_history"]["global_history_scan_status"] = "not_scanned_saved_cohort_only"
        else:
            report["point_history"]["documents_total"] = session.query(TennisPointDocument).filter(
                TennisPointDocument.retrieved_at <= now,
            ).count()
            report["point_history"]["availability_unknown_documents"] = session.query(TennisPointDocument).filter(
                TennisPointDocument.retrieved_at <= now, TennisPointDocument.available_at.is_(None)
            ).count()
        version_keys = session.query(TennisPointHistoryVersion.id).filter(
            TennisPointHistoryVersion.source_match_id.in_(source_ids),
            TennisPointHistoryVersion.available_at.is_not(None),
            TennisPointHistoryVersion.available_at <= now,
        ).order_by(TennisPointHistoryVersion.available_at, TennisPointHistoryVersion.id).all() if source_ids else []
        versions = _history_versions_by_id(session, [row.id for row in version_keys])
        report["point_history"]["documents_linked"] = session.query(TennisPointDocument).filter(
            TennisPointDocument.source_match_id.in_(source_ids) if source_ids else False,
            TennisPointDocument.retrieved_at <= now,
        ).count()
        latest = {}
        source_ids_with_versions = set()
        for version in versions:
            report["point_history"]["parsed_versions_linked"] += 1
            source_ids_with_versions.add(version.source_match_id)
            if not is_authoritative_point_history_version(version.events_json, version.quality_json):
                continue
            matching_events = [event_id for event_id, ids in source_ids_by_event.items() if version.source_match_id in ids]
            for event_id in matching_events:
                set_numbers = {version.quality_json.get("set_number")} if isinstance(version.quality_json, dict) and version.quality_json.get("set_number") else set()
                set_numbers.update(item.get("set") for item in (version.events_json or []) if item.get("set") is not None)
                for set_no in set_numbers:
                    latest[(event_id, version.source_match_id, set_no)] = version

        direct_api = _load_direct_api_histories(session, eligible, now)
        report['point_history']['direct_api_links'] = len(direct_api)
        for context in direct_api:
            version, reference = context['version'], context['reference']
            event_id = version.fonbet_event_id
            source_contexts[reference] = context
            source_ids_by_event[event_id].add(reference)
            source_ids_with_versions.add(reference)
            orientation[(event_id, reference)] = True  # Stored v2 rows are already in Fonbet A/B order.
            set_numbers = set(version.quality_json.get('set_numbers') or [])
            set_numbers.update(row.get('set') for row in version.events_json if row.get('set') is not None)
            for number in set_numbers:
                latest[(event_id, reference, number)] = version
        report['point_history']['linked_fonbet_events'] = len(source_ids_by_event)

        # Exact live-state joins: ambiguous repeated score states and unknown side order are excluded.
        event_ids_with_history = {event_id for event_id, _, _ in latest}
        event_ids_with_snapshots = set()
        representative_snapshots = {}
        valid_match_ids = [event_id for event_id, winner in result_labels.items() if winner is not None]
        for start in range(0, len(valid_match_ids) if indexed_snapshots is None else 0, 2000):
            batch = valid_match_ids[start:start + 2000]
            # A LATERAL lookup repeats each hypertable chunk scan once per
            # requested event. Use one indexed pass over each chunk and retain
            # its first observation per event instead.
            rows = session.execute(text("""
                SELECT DISTINCT ON (event_id)
                       id, observed_at, event_id, score_detail, sport_state,
                       current_period, raw_hash, raw_object_key
                FROM event_state_snapshots
                WHERE sport_code='tennis' AND event_id = ANY(CAST(:event_ids AS uuid[]))
                ORDER BY event_id, observed_at, id
            """), {"event_ids": batch}).all()
            for row in rows:
                snapshot_rows_by_id[row.id] = row
                event_ids_with_snapshots.add(row.event_id)
                representative_snapshots[row.event_id] = row
        snapshots_by_event = defaultdict(list)
        game_snapshots_by_event = defaultdict(list)
        ids_to_read = list(event_ids_with_history)
        if indexed_snapshots is not None:
            indexed_by_event = defaultdict(list)
            for row in sorted(indexed_snapshots, key=lambda item: (item.observed_at, str(item.id))):
                snapshot_rows_by_id[row.id] = row
                if row.event_id not in eligible:
                    continue
                event_ids_with_snapshots.add(row.event_id)
                indexed_by_event[row.event_id].append(row)
                state = _snapshot_state(row)
                if state is not None and row.event_id in event_ids_with_history:
                    snapshots_by_event[row.event_id].append((state, row.id, row.observed_at))
                game_state = _snapshot_game_state(row)
                if game_state is not None and row.event_id in event_ids_with_history:
                    game_snapshots_by_event[row.event_id].append((game_state, row.id, row.observed_at))
            for event_id, rows in indexed_by_event.items():
                if result_labels.get(event_id) not in (0, 1):
                    continue
                official = (eligible[event_id].metadata_json or {}).get("fonbet_official_result") or {}
                selected = _select_pre_result_snapshot(
                    rows, feature_check=lambda row: feature_availability(event_id, row.id, row.observed_at),
                    label_available_at=result_times.get(event_id), final_score=official.get("final_score"))
                if selected is not None:
                    representative_snapshots[event_id] = selected
            # Training examples below require labels and PBP alignment. PIT
            # collection monitoring must also cover current/unlabelled events,
            # so resolve the source groups independently for every indexed
            # snapshot before constructing labelled training examples.
            indexed_coverage_rows = []
            for row in indexed_snapshots:
                if row.event_id not in eligible:
                    continue
                if defer_admission_windows and result_labels.get(row.event_id) not in (0,1):
                    continue
                if defer_admission_windows and result_times.get(row.event_id) is not None and row.observed_at>=result_times[row.event_id]:
                    continue
                indexed_coverage_rows.append({
                    "fonbet_event_id": str(row.event_id),
                    "snapshot_id": str(row.id),
                    "snapshot_observed_at": row.observed_at.isoformat(),
                    "feature_availability": feature_availability(row.event_id, row.id, row.observed_at),
                })
            report["snapshot_source_coverage"]["skipped_outside_source_scope"] = (
                len(indexed_snapshots) - len(indexed_coverage_rows))
            coverage_summary = _summarize_snapshot_source_coverage([
                item["feature_availability"] for item in indexed_coverage_rows
            ])
            report["snapshot_source_coverage"].update(coverage_summary)
            report["snapshot_source_coverage"]["checked_snapshots"] = len(indexed_coverage_rows)
            if snapshot_coverage_output_dir is not None:
                report["snapshot_source_coverage"]["per_example_results"] = _write_snapshot_source_coverage(
                    indexed_coverage_rows, snapshot_coverage_output_dir)
        for start in range(0, len(ids_to_read) if indexed_snapshots is None else 0, 200):
            batch = ids_to_read[start:start + 200]
            rows = session.query(EventStateSnapshot).filter(EventStateSnapshot.event_id.in_(batch)).order_by(
                EventStateSnapshot.event_id, EventStateSnapshot.observed_at, EventStateSnapshot.id
            ).yield_per(5000)
            for row in rows:
                snapshot_rows_by_id[row.id] = row
                state = _snapshot_state(row)
                if state is not None:
                    snapshots_by_event[row.event_id].append((state, row.id, row.observed_at))
                game_state = _snapshot_game_state(row)
                if game_state is not None:
                    game_snapshots_by_event[row.event_id].append((game_state, row.id, row.observed_at))

        # Set labels can be recovered from a validated official Fonbet result;
        # they do not require a Flashscore PBP tape. Page snapshots by event and
        # retain one row per distinct observed state to bound memory and query load.
        set_snapshots_by_event = defaultdict(dict)

        def retain_set_snapshot(row):
            set_number = _snapshot_set_number(row)
            if set_number is None:
                return
            state = _snapshot_state(row)
            key = (set_number, state)
            current = set_snapshots_by_event[row.event_id].get(key)
            if current is None or (row.observed_at, str(row.id)) < (current.observed_at, str(current.id)):
                set_snapshots_by_event[row.event_id][key] = row

        if indexed_snapshots is not None:
            for row in indexed_snapshots:
                retain_set_snapshot(row)
        else:
            for start in range(0, len(valid_match_ids), event_batch_size):
                event_batch = sorted(valid_match_ids[start:start + event_batch_size], key=str)
                cursor = None
                while event_batch:
                    query = session.query(EventStateSnapshot).filter(
                        EventStateSnapshot.event_id.in_(event_batch),
                        EventStateSnapshot.sport_code == "tennis",
                        EventStateSnapshot.status == "live",
                        EventStateSnapshot.observed_at <= now,
                    )
                    if cursor is not None:
                        query = query.filter(tuple_(EventStateSnapshot.event_id,
                            EventStateSnapshot.observed_at, EventStateSnapshot.id) > tuple_(*cursor))
                    page = query.order_by(EventStateSnapshot.event_id,
                        EventStateSnapshot.observed_at, EventStateSnapshot.id).limit(snapshot_page_size).all()
                    if not page:
                        break
                    for row in page:
                        snapshot_rows_by_id[row.id] = row
                        event_ids_with_snapshots.add(row.event_id)
                        retain_set_snapshot(row)
                    last = page[-1]
                    cursor = (last.event_id, last.observed_at, last.id)
                    if len(page) < snapshot_page_size:
                        break

        parsed_label_keys = {name: set() for name in ("point", "game", "set", "match")}
        parsed_label_keys["match"].update((event_id, result_labels[event_id])
            for event_id in eligible if result_labels.get(event_id) in (0, 1))
        aligned_label_keys = {name: set() for name in ("point", "game", "set", "match")}
        provenance = {name: Counter() for name in ("point", "game", "set")}
        ambiguous_transition_events = set()
        example_provenance = {}
        official_set_scores = {}
        for event_id, event in eligible.items():
            if result_labels.get(event_id) in (0, 1):
                scores, _reason = _official_set_scores(event)
                if scores is not None:
                    official_set_scores[event_id] = scores
                    for set_no, score in enumerate(scores, 1):
                        parsed_label_keys["set"].add((event_id, set_no, tuple(score)))

        def record_example(target, identity, event_id, label, snapshot_id, snapshot_at,
                           version=None, source_id=None, item=None):
            key = (target, identity)
            if key in example_provenance:
                if not filter_unusable_examples:
                    return
                check = feature_availability(event_id, snapshot_id, snapshot_at)
                values = feature_values_by_event_snapshot.get((event_id,snapshot_id))
                old = example_provenance[key]
                if _usable_training_example(old) or not (check.get('certified') and not _missing_feature_values(values)):
                    return
            source_label_times = [result_times.get(event_id)]
            if version is not None:
                source_label_times.append(version.available_at)
                linked_at = link_available_at_by_event_source.get((event_id, source_id))
                if linked_at:
                    try:
                        source_label_times.append(datetime.fromisoformat(linked_at.replace("Z", "+00:00")))
                    except ValueError:
                        pass
            known_label_times = [value for value in source_label_times if value is not None]
            label_available_at = max(known_label_times) if known_label_times else None
            example_provenance[key] = {
                "target": target, "fonbet_event_id": str(event_id),
                "fonbet_source_event_id": str(eligible[event_id].source_event_id),
                "snapshot_id": str(snapshot_id), "snapshot_observed_at": snapshot_at.isoformat(),
                "source": source_contexts.get(source_id, {}).get('source') if source_id else (item or {}).get('source'),
                "source_match_id": source_contexts.get(source_id, {}).get('source_match_id', str(source_id)) if source_id else None,
                "source_match_reference": source_id,
                "history_link_version_id": str(version.id) if version and source_contexts.get(source_id, {}).get('source') == 'api_tennis' else None,
                "history_link_evidence": source_contexts.get(source_id, {}).get('evidence'),
                "point_document_id": str(version.document_id) if version else None,
                "point_version_id": str(version.id) if version else None,
                "match_link_id": link_ids_by_event_source.get((event_id, source_id)) if source_id else None,
                "match_link_available_at": link_available_at_by_event_source.get((event_id, source_id)) if source_id else None,
                "set_number": item.get("set") if item else None,
                "game_score": item.get("game_score") if item else None,
                "game_score_before": item.get("game_score_before") if item else None,
                "target_state": (_snapshot_state(snapshot_rows_by_id[snapshot_id]) if target == 'point'
                                 else _snapshot_game_state(snapshot_rows_by_id[snapshot_id]) if target == 'game'
                                 else item.get('set') if item else None),
                "alignment_policy": "confirmed_pre_game_score_v1",
                "point_index": item.get("point_index") if item else None,
                "source_point_server_known": item.get("server") in (0, 1) if item else None,
                "label": int(label),
                "label_available_at": label_available_at.isoformat() if label_available_at else None,
                "result_time_source": result_time_sources.get(event_id),
                "result_time_snapshot_evidence": ({
                    **result_snapshot_evidence[event_id],
                    "observed_at": result_snapshot_evidence[event_id]["observed_at"].isoformat(),
                } if event_id in result_snapshot_evidence else None),
                "retrospective": bool(version.retrospective) if version else False,
                "feature_availability": feature_availability(event_id, snapshot_id, snapshot_at),
                "feature_values": feature_values_by_event_snapshot.get((event_id, snapshot_id)),
                "feature_missing_fields": _missing_feature_values(
                    feature_values_by_event_snapshot.get((event_id, snapshot_id))),
            }
        for (event_id, source_id, set_no), version in latest.items():
            event = eligible.get(event_id)
            side_order = orientation.get((event_id, source_id))
            if event is None:
                continue
            category = groups[event_id]
            selected = {"by_tour": category[0], "by_discipline": category[1], "by_competition": category[2]}
            source_events = _point_events_for_alignment(version.events_json or [], version.parser_version)
            source_events = [item for item in source_events if item.get("set") == set_no]
            terminal_qualities = {"terminal_from_game_result", "terminal_from_tiebreak_result", "confirmed_game_result"}
            confirmed = [item for item in source_events if item.get("confirmed") and item.get("winner") in (0, 1)
                         and item.get("quality") not in terminal_qualities]
            terminal_games = [item for item in source_events
                              if item.get("confirmed") and item.get("winner") in (0, 1)
                              and item.get("quality") in {"terminal_from_game_result", "confirmed_game_result"}]
            terminal_tiebreaks = [item for item in source_events
                                   if item.get("confirmed") and item.get("winner") in (0, 1)
                                   and item.get("quality") == "terminal_from_tiebreak_result"]
            for item in confirmed:
                identity = (event_id, source_id, item.get("set"), tuple(item.get("game_score") or []),
                    item.get("point_index"), tuple(item.get("state_before") or []), item.get("tie_break", False))
                parsed_label_keys["point"].add(identity)
                provenance["point"]["retrospective" if version.retrospective else "observed"] += 1
            for item in terminal_games:
                game_identity = (event_id, source_id, item.get("set"), tuple(item.get("game_score") or []))
                parsed_label_keys["game"].add(game_identity)
                provenance["game"]["retrospective" if version.retrospective else "observed"] += 1
            for item in terminal_tiebreaks:
                game_identity = (event_id, source_id, item.get("set"), tuple(item.get("game_score") or []))
                parsed_label_keys["game"].add(game_identity)
                provenance["game"]["retrospective" if version.retrospective else "observed"] += 1
            terminal_sets = terminal_games + terminal_tiebreaks
            for item in terminal_sets:
                if side_order is None:
                    exclusions["set_side_order_unknown"] += 1
                    continue
                score = item.get("game_score")
                if isinstance(score, list) and len(score) == 2 and _is_finished_set(int(score[0]), int(score[1])):
                    set_no = item.get("set")
                    official_scores = official_set_scores.get(event_id, [])
                    oriented_score = (tuple(score) if side_order else tuple(reversed(score))) \
                        if side_order is not None else tuple(score)
                    if side_order is not None and (
                            not isinstance(set_no, int) or set_no < 1 or set_no > len(official_scores)
                            or oriented_score != tuple(official_scores[set_no - 1])):
                        exclusions["official_pbp_set_score_conflict"] += 1
                        continue
                    parsed_label_keys["set"].add((event_id, set_no, oriented_score))
                    provenance["set"]["retrospective" if version.retrospective else "observed"] += 1
            if side_order is None:
                exclusions["point_side_order_unknown"] += len(confirmed)
                continue
            source_point_events = confirmed
            source_point_signatures = [_source_point_key(item, side_order) for item in source_point_events]
            signatures = Counter(source_point_signatures)
            ambiguous_count = _ambiguous_source_point_transition_count(source_point_events, side_order)
            exclusions["ambiguous_point_transition"] += ambiguous_count
            if any(signature is None or signatures[signature] > 1 for signature in source_point_signatures):
                ambiguous_transition_events.add(event_id)
            for item in source_point_events:
                signature = _source_point_key(item, side_order)
                if signature is None or signatures[signature] != 1:
                    continue
                next_winner = item.get("winner") if side_order else 1 - item.get("winner")
                for snapshot_state, snapshot_id, snapshot_at in snapshots_by_event.get(event_id, []):
                    if _snapshot_matches_source_point(snapshot_state, signature):
                        identity = (event_id, source_id, set_no, signature, next_winner)
                        aligned_label_keys["point"].add(identity)
                        record_example("point", identity, event_id, next_winner, snapshot_id,
                                       snapshot_at, version, source_id, item)

            # Set and match labels are attached only to real Fonbet snapshots in the event.
            for item in terminal_sets:
                score = item.get("game_score")
                if not isinstance(score, list) or len(score) != 2:
                    continue
                item_set_no = item.get("set")
                official_scores = official_set_scores.get(event_id, [])
                terminal = _validated_terminal_game(item, side_order, official_scores)
                if terminal is None:
                    exclusions["invalid_terminal_game"] += 1
                    continue
                item_set_no, before, winner, completed_set = terminal
                game_key = (item_set_no, before)
                for snapshot_game_state, snapshot_id, snapshot_at in game_snapshots_by_event.get(event_id, []):
                    if snapshot_game_state == game_key:
                        identity = (event_id, source_id, game_key, winner)
                        aligned_label_keys["game"].add(identity)
                        record_example("game", identity, event_id, winner, snapshot_id,
                                       snapshot_at, version, source_id, item)
                if completed_set:
                    for snapshot_state, snapshot_id, snapshot_at in snapshots_by_event.get(event_id, []):
                        if snapshot_state[0] == item_set_no:
                            identity = (event_id, item_set_no, snapshot_state, winner)
                            aligned_label_keys["set"].add(identity)
                            record_example("set", identity, event_id, winner, snapshot_id,
                                           snapshot_at, version, source_id, item)

        # Fonbet's validated final set score is sufficient for set winners.
        # Use pre-result snapshots from each set, independent of external PBP.
        for event_id, scores in official_set_scores.items():
            label_available_at = result_times.get(event_id)
            if label_available_at is None:
                exclusions["set_result_time_unknown"] += 1
                continue
            for snapshot in set_snapshots_by_event.get(event_id, {}).values():
                set_no = _snapshot_set_number(snapshot)
                if set_no is None or set_no < 1 or set_no > len(scores):
                    exclusions["set_snapshot_period_unknown"] += 1
                    continue
                if snapshot.observed_at >= label_available_at:
                    continue
                state = _snapshot_state(snapshot)
                if state is not None and tuple(state[1]) == tuple(scores[set_no - 1]):
                    continue
                score = scores[set_no - 1]
                winner = 0 if score[0] > score[1] else 1
                identity = (event_id, set_no, state, winner)
                parsed_label_keys["set"].add((event_id, set_no, tuple(score)))
                aligned_label_keys["set"].add(identity)
                record_example("set", identity, event_id, winner, snapshot.id,
                    snapshot.observed_at, item={"set": set_no, "game_score": list(score),
                    "source": "fonbet_official_result"})

        example_provenance, source_conflicts = _resolve_source_examples(example_provenance)
        exclusions['conflicting_source_labels'] += len(source_conflicts)
        report['source_conflicts_manifest'] = _write_audit_jsonl(source_conflicts, training_examples_output_dir,
                                                               'tennis_source_label_conflicts')
        for target in ("point", "game", "set"):
            aligned_label_keys[target] = {key[1] for key in example_provenance if key[0] == target}
            report["targets"][target]["parsed_labels"] = len(parsed_label_keys[target])
            report["targets"][target]["training_examples"] = len(aligned_label_keys[target])
            report["targets"][target]["labelled_matches"] = len({row[0] for row in aligned_label_keys[target]})
            report["targets"][target]["retrospective_examples"] = provenance[target]["retrospective"]
            report["targets"][target]["observed_examples"] = provenance[target]["observed"]
        for event_id in valid_match_ids:
            if event_id in event_ids_with_snapshots and event_id not in representative_snapshots:
                exclusions["no_pre_result_live_snapshot"] += 1
            if event_id in representative_snapshots:
                identity = (event_id, result_labels[event_id])
                aligned_label_keys["match"].add(identity)
                snapshot = representative_snapshots[event_id]
                source_id = next(iter(sorted(source_ids_by_event.get(event_id, []))), None)
                example_provenance[("match", identity)] = {
                    "target": "match", "fonbet_event_id": str(event_id),
                    "fonbet_source_event_id": str(eligible[event_id].source_event_id),
                    "snapshot_id": str(snapshot.id), "snapshot_observed_at": snapshot.observed_at.isoformat(),
                    "source_match_id": source_id,
                    "point_document_id": None, "point_version_id": None,
                    "match_link_id": link_ids_by_event_source.get((event_id, source_id)) if source_id else None,
                    "match_link_available_at": link_available_at_by_event_source.get((event_id, source_id)) if source_id else None,
                    "set_number": None, "game_score": None, "point_index": None,
                    "label": int(result_labels[event_id]),
                    "label_available_at": result_times[event_id].isoformat()
                        if result_times.get(event_id) else None,
                    "result_time_source": result_time_sources.get(event_id),
                    "result_time_snapshot_evidence": ({
                        **result_snapshot_evidence[event_id],
                        "observed_at": result_snapshot_evidence[event_id]["observed_at"].isoformat(),
                    } if event_id in result_snapshot_evidence else None),
                    "retrospective": False,
                    "feature_availability": feature_availability(event_id, snapshot.id, snapshot.observed_at),
                    "feature_values": feature_values_by_event_snapshot.get((event_id, snapshot.id)),
                    "feature_missing_fields": _missing_feature_values(
                        feature_values_by_event_snapshot.get((event_id, snapshot.id))),
                }
        report["targets"]["match"]["training_examples"] = len(aligned_label_keys["match"])
        report["targets"]["match"]["labelled_matches"] = len({row[0] for row in aligned_label_keys["match"]})
        for target in ("point", "game", "set", "match"):
            label_keys = aligned_label_keys[target]
            report["targets"][target]["class_a"] = sum(row[-1] == 0 for row in label_keys)
            report["targets"][target]["class_b"] = sum(row[-1] == 1 for row in label_keys)
        report["exclusions"] = dict(sorted(exclusions.items()))
        for target in ("point", "game", "set", "match"):
            report["targets"][target]["eligible_matches"] = len(eligible)
        for key in ("by_tour", "by_discipline", "by_competition"):
            for bucket in report[key].values():
                bucket["point_examples"] = 0
                bucket["game_examples"] = 0
                bucket["set_examples"] = 0
        for target, field in (("point", "point_examples"), ("game", "game_examples"), ("set", "set_examples")):
            for row in aligned_label_keys[target]:
                event_id = row[0]
                tour_name, discipline_name, competition_name = groups[event_id]
                for key, name in (("by_tour", tour_name), ("by_discipline", discipline_name), ("by_competition", competition_name)):
                    report[key][name][field] += 1
        target_rows = {name: aligned_label_keys[name] for name in ("point", "game", "set", "match")}
        target_temporal = defaultdict(dict)
        for row in example_provenance.values():
            target = row["target"]
            event_id = uuid.UUID(row["fonbet_event_id"])
            temporal = target_temporal[target].setdefault(event_id, {"label_available_at": None, "forecast_at": None})
            label_at = row.get("label_available_at")
            forecast_at = row.get("snapshot_observed_at")
            if label_at:
                value = datetime.fromisoformat(label_at.replace("Z", "+00:00"))
                current = temporal["label_available_at"]
                if current is None or value > current:
                    temporal["label_available_at"] = value
            if forecast_at:
                value = datetime.fromisoformat(forecast_at.replace("Z", "+00:00"))
                current = temporal["forecast_at"]
                if current is None or value < current:
                    temporal["forecast_at"] = value
        manifest_event_times = {}
        for event_id in eligible:
            if result_labels.get(event_id) not in (0, 1):
                continue
            forecast_times = [value["forecast_at"] for value in
                              (target_temporal.get(target, {}).get(event_id, {}) for target in target_rows)
                              if value.get("forecast_at") is not None]
            if forecast_times:
                manifest_event_times[event_id] = min(forecast_times)
        if filter_unusable_examples:
            original_examples = list(example_provenance.values())
            usable_examples = [r for r in original_examples if _usable_training_example(r)]
            rejected_examples = [{**r,'rejection_reasons':_example_rejection_reasons(r)}
                                 for r in original_examples if not _usable_training_example(r)]
            report['candidate_targets'] = {target:dict(values) for target,values in report['targets'].items()}
            report['rejected_examples_manifest'] = _write_audit_jsonl(
                rejected_examples, training_examples_output_dir, 'tennis_rejected_feature_examples')
            report['example_filter'] = {'candidate_examples':len(original_examples),
                'usable_examples':len(usable_examples),'rejected_feature_examples':len(rejected_examples)}
            target_rows = {target:set() for target in target_rows}
            target_temporal = defaultdict(dict)
            for row in usable_examples:
                target = row['target']; event_id = uuid.UUID(row['fonbet_event_id'])
                target_rows[target].add((event_id,row['snapshot_id'],row['label']))
                temporal = target_temporal[target].setdefault(event_id,{'label_available_at':None,'forecast_at':None})
                at = datetime.fromisoformat(row['snapshot_observed_at'].replace('Z','+00:00'))
                temporal['forecast_at'] = min(temporal['forecast_at'],at) if temporal['forecast_at'] else at
                if row.get('label_available_at'):
                    at = datetime.fromisoformat(row['label_available_at'].replace('Z','+00:00'))
                    temporal['label_available_at'] = max(temporal['label_available_at'],at) if temporal['label_available_at'] else at
            for target,keys in target_rows.items():
                report['targets'][target].update(training_examples=sum(r['target']==target for r in usable_examples),
                    labelled_matches=len({r[0] for r in keys}),class_a=sum(r['target']==target and r['label']==0 for r in usable_examples),
                    class_b=sum(r['target']==target and r['label']==1 for r in usable_examples))
            manifest_event_times = {event_id:min(values['forecast_at'] for per_target in target_temporal.values()
                for eid,values in per_target.items() if eid==event_id and values['forecast_at'] is not None)
                for event_id in {uuid.UUID(r['fonbet_event_id']) for r in usable_examples}}
        if defer_admission_windows:
            window_report = {target:{'folds':[],'folds_meet_minimums':False,'holdout_meets_minimums':False}
                             for target in target_rows}
            manifest_metadata = {'status':'deferred_until_full_census_complete'}
        else:
            window_report, manifest_metadata = _walk_forward_admission(
                target_rows, manifest_event_times, target_temporal)
        report["training_gate"]["walk_forward"] = window_report
        report["training_gate"]["manifest"] = manifest_metadata
        candidate_example_rows = [example_provenance[key] for key in sorted(example_provenance, key=lambda item: (item[0], repr(item[1])))]
        example_rows = ([r for r in candidate_example_rows if _usable_training_example(r)]
                        if filter_unusable_examples else candidate_example_rows)
        feature_report = report["feature_availability"]
        feature_checks = [row.get("feature_availability", {}) for row in example_rows]
        feature_report.update(_summarize_feature_availability(feature_checks))
        feature_report["feature_values_materialized_examples"] = sum(
            not _missing_feature_values(row.get("feature_values")) for row in example_rows
        )
        feature_report["feature_values_missing_examples"] = (
            len(example_rows) - feature_report["feature_values_materialized_examples"]
        )
        feature_report["feature_value_missing_field_counts"] = dict(Counter(
            field for row in example_rows
            for field in _missing_feature_values(row.get("feature_values"))))
        for check in feature_checks:
            source_times = check.get("source_available_at", {})
            resolution = check.get("source_resolution", {})
            defaulted_sources = set(check.get("contract_defaulted_sources", []))
            for group_name, resolution_value in resolution.items():
                if isinstance(resolution_value, dict):
                    for side, side_resolution in resolution_value.items():
                        key = f"{group_name}.{side}:{side_resolution}"
                        feature_report["resolution_counts"][key] = feature_report["resolution_counts"].get(key, 0) + 1
                else:
                    key = f"{group_name}:{resolution_value}"
                    feature_report["resolution_counts"][key] = feature_report["resolution_counts"].get(key, 0) + 1
            optional_unknown = set(check.get("optional_unknown_groups", []))
            for name in PIT_FEATURE_SOURCE_GROUPS:
                group = feature_report["source_groups"].setdefault(
                    name, {"available": 0, "contract_defaulted": 0, "missing_or_unverifiable": 0,
                           "explicitly_unknown": 0})
                if name in defaulted_sources:
                    group["contract_defaulted"] += 1
                elif name in optional_unknown:
                    group["explicitly_unknown"] += 1
                elif source_times.get(name) is None:
                    group["missing_or_unverifiable"] += 1
                else:
                    group["available"] += 1
            for name in check.get("missing_sources", []) + check.get("future_sources", []):
                feature_report["exclusions"][name] = feature_report["exclusions"].get(name, 0) + 1
        parsed_matches = {target: {row[0] for row in keys}
                          for target, keys in parsed_label_keys.items()}
        aligned_matches = {target: {row[0] for row in keys}
                           for target, keys in aligned_label_keys.items()}
        example_rows_by_target_event = defaultdict(list)
        for row in candidate_example_rows:
            example_rows_by_target_event[(row["target"], uuid.UUID(row["fonbet_event_id"]))].append(row)
        certified_matches = {target: {
            event_id for (row_target, event_id), rows in example_rows_by_target_event.items()
            if row_target == target and rows and (any if filter_unusable_examples else all)(
                _usable_training_example(row) for row in rows)
        } for target in ("point", "game", "set", "match")}
        linked_match_ids = set(source_ids_by_event)
        authoritative_match_ids = {event_id for event_id, _source_id, _set_no in latest}
        orientation_match_ids = {event_id for (event_id, _source_id), value in orientation.items()
                                  if value is not None}
        match_diagnostic_rows = []
        for event_id in sorted(eligible, key=str):
            target_status = {}
            for target in ("point", "game", "set", "match"):
                failures = []
                if event_id in late_result_ids:
                    failures.append("result_after_audit_cutoff")
                elif result_labels.get(event_id) not in (0, 1):
                    failures.append("match_result_invalid")
                if target in ("point", "game") and not failures:
                    if event_id not in linked_match_ids:
                        failures.append("no_confirmed_source_link")
                    elif not (source_ids_by_event.get(event_id, set()) & source_ids_with_versions):
                        failures.append("no_history_version")
                    elif event_id not in authoritative_match_ids:
                        failures.append("history_not_authoritative")
                    elif event_id not in orientation_match_ids:
                        failures.append("unknown_side_order")
                if target == "set" and not failures:
                    if event_id not in official_set_scores:
                        failures.append("official_set_score_unavailable")
                    elif result_times.get(event_id) is None:
                        failures.append("label_time_unknown")
                if event_id not in parsed_matches[target] and not failures:
                    failures.append("no_parsed_target_label")
                if (target == "point" and event_id in ambiguous_transition_events
                        and event_id not in aligned_matches[target]):
                    failures.append("ambiguous_transition")
                if event_id not in aligned_matches[target] and not failures:
                    failures.append("no_snapshot_aligned_label")
                if event_id in aligned_matches[target] and event_id not in certified_matches[target]:
                    failures.append("feature_pit_not_certified")
                examples = example_rows_by_target_event.get((target, event_id), [])
                if filter_unusable_examples and examples and event_id not in certified_matches[target]:
                    for row in examples:
                        if 'snapshot_not_before_label' in _example_rejection_reasons(row):
                            failures.insert(0,'snapshot_not_before_label')
                            break
                if examples and any(not row.get("label_available_at") for row in examples):
                    failures.append("label_time_unknown")
                primary = failures[0] if failures else "feature_certified_candidate"
                secondary = sorted(set(failures[1:]))
                target_status[target] = {"status": primary,
                                         "additional_reasons": secondary}
                stage_counts = report["target_diagnostics"][target]["stage_exclusions"]
                stage_counts[primary] = stage_counts.get(primary, 0) + 1
                secondary_counts = report["target_diagnostics"][target]["secondary_exclusions"]
                for reason in secondary:
                    secondary_counts[reason] = secondary_counts.get(reason, 0) + 1
            match_diagnostic_rows.append({"fonbet_event_id": str(event_id), "targets": target_status})

        for excluded in out_of_scope_diagnostics:
            match_diagnostic_rows.append({'fonbet_event_id':excluded['fonbet_event_id'],
                'fonbet_source_event_id':excluded['fonbet_source_event_id'],
                'targets':{target:{'status':excluded['reason'],'additional_reasons':[]}
                           for target in ('point','game','set','match')}})
            for target in ('point','game','set','match'):
                counts = report['target_diagnostics'][target]['stage_exclusions']
                counts[excluded['reason']] = counts.get(excluded['reason'],0)+1

        for target in ("point", "game", "set", "match"):
            diagnostics = report["target_diagnostics"][target]
            diagnostics["unique_matches_with_parsed_labels"] = len(parsed_matches[target])
            diagnostics["unique_matches_with_snapshot_aligned_labels"] = len(aligned_matches[target])
            diagnostics["unique_matches_with_feature_certified_labels"] = len(certified_matches[target])
            diagnostics["unique_matches_with_causal_train_labels_by_fold"] = [
                fold.get("train", {}).get("matches", 0)
                for fold in report["training_gate"].get("walk_forward", {}).get(target, {}).get("folds", [])]

        report["match_exclusions_manifest"] = _write_audit_jsonl(
            match_diagnostic_rows, training_examples_output_dir, "tennis_match_target_diagnostics")
        report["training_examples_manifest"] = _write_training_examples(
            example_rows, training_examples_output_dir
        )
        report["training_gate"]["chronology_timestamp_missing_matches"] = len({
            row["fonbet_event_id"] for row in example_rows if not row.get("label_available_at")
        })
        report["training_gate"]["result_availability_unknown_matches"] = sum(
            result_labels.get(event_id) in (0, 1) and result_times.get(event_id) is None
            for event_id in eligible)
        report["training_gate"]["unknown_result_time_policy"] = (
            "keep_confirmed_labels_in_evaluation_windows; purge from each training fold unless source availability precedes that fold")
        report["training_gate"]["unknown_result_time_matches"] = sum(
            result_labels.get(event_id) in (0, 1) and result_times.get(event_id) is None
            for event_id in eligible
        )
        report["training_gate"]["unknown_result_time_purged_from_training_folds"] = sum(
            fold.get("training_filter", {}).get("purged_unknown_label_time_matches", 0)
            for target in window_report.values() for fold in target.get("folds", []))
        report["training_gate"]["scheduled_time_used_as_chronology"] = sum(
            source == "scheduled_start_at" for source in result_time_sources.values())
        report["training_gate"]["result_time_sources"] = report["result_time_evidence"]["sources"]
        report["training_gate"]["windows_evaluated"] = not defer_admission_windows
        windows_ok = all(item["folds_meet_minimums"] and item["holdout_meets_minimums"] for item in window_report.values())
        temporal_ok = all(
            bool(item["folds"]) and all(fold["temporal_precedence"]["passed"] for fold in item["folds"])
            for item in window_report.values())
        report["training_gate"]["manifest_result_availability_certified"] = temporal_ok
        base_coverage_ok = all(
            report["targets"][name]["labelled_matches"] >= 500
            and report["targets"][name]["class_a"] > 0 and report["targets"][name]["class_b"] > 0
            for name in ("point", "game", "set", "match")
        )
        report["training_gate"]["base_coverage_status"] = "sufficient_for_window_assessment" if base_coverage_ok else "insufficient"
        # This audit now checks feature source times for every emitted
        # example. Missing historical source provenance is a blocking failure.
        report["training_gate"]["point_in_time_certified"] = (
            feature_report["checked_examples"] > 0
            and feature_report["uncertified_examples"] == 0
            and feature_report["feature_values_missing_examples"] == 0
        )
        status, reasons = _training_gate_status(
            base_coverage_ok=base_coverage_ok,
            windows_ok=windows_ok,
            temporal_ok=temporal_ok,
            point_in_time_ok=report["training_gate"]["point_in_time_certified"],
            manifest_result_times_certified=report["training_gate"]["manifest_result_availability_certified"],
        )
        report["training_gate"]["status"] = status
        report["training_gate"]["reasons"] = reasons
        if discipline_scope == "doubles":
            report["training_gate"]["reasons"].append("doubles_feature_contract_not_certified")
            report["training_gate"]["status"] = "blocked"
        if cohort:
            report["training_gate"]["reasons"].append("full_database_census_not_completed")
            report["training_gate"]["status"] = "blocked"
            report["audit_scope"]["status"] = "saved_cohort_complete"
            report["audit_scope"]["as_of"] = now.isoformat()
        else:
            report["audit_scope"].update(status="complete", finished_at=datetime.now(timezone.utc).isoformat())
    return report
