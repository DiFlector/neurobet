"""Strict verification for recovering links to original Fonbet live snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable

from contracts.tennis_pit import content_addressed_object_matches_hash


PARSER_VERSION = "tennis-fonbet-state-reparse-v1"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _without_timestamps(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_timestamps(item) for key, item in value.items() if key != "timestamp"}
    if isinstance(value, list):
        return [_without_timestamps(item) for item in value]
    return value


def _parse_state(raw_event: dict) -> dict:
    from tennis_adapter.adapter import TennisSportAdapter

    return TennisSportAdapter().parse_state(raw_event).model_dump(mode="json")


def verify_fonbet_snapshot_evidence(
    *, raw_key: str, body: bytes, source_event_id: str, snapshot_at: datetime,
    expected_score: str | None, expected_state: dict, state_parser: Callable[[dict], dict] | None = None,
) -> dict[str, Any]:
    """Return a provenance envelope only when source bytes prove the saved state by cutoff."""
    cutoff = _utc(snapshot_at)
    try:
        document = json.loads(body)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return {"verified": False, "reason": "invalid_json"}
    if not isinstance(document, dict) or document.get("source") != "fonbet":
        return {"verified": False, "reason": "wrong_source"}
    if document.get("sport_code") != "tennis" or document.get("page_type") != "live":
        return {"verified": False, "reason": "wrong_snapshot_type"}
    raw_at = document.get("collected_at")
    try:
        available_at = datetime.fromisoformat(str(raw_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return {"verified": False, "reason": "collection_time_missing_or_invalid"}
    if available_at.tzinfo is None:
        return {"verified": False, "reason": "collection_timezone_missing"}
    available_at = _utc(available_at)
    if available_at > cutoff:
        return {"verified": False, "reason": "source_after_snapshot"}

    digest = str(document.get("content_hash") or "")
    if not content_addressed_object_matches_hash(digest, raw_key):
        return {"verified": False, "reason": "object_key_hash_mismatch"}
    try:
        normalized_payload = {
            "source": document["source"], "sport_code": document["sport_code"],
            "url": document["url"], "events": document["events"],
        }
        actual_digest = hashlib.sha256(json.dumps(
            normalized_payload, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")).hexdigest()
    except (KeyError, TypeError, ValueError):
        return {"verified": False, "reason": "normalized_payload_invalid"}
    if digest.removeprefix("sha256:").lower() != actual_digest:
        return {"verified": False, "reason": "content_hash_invalid"}
    rows = document.get("events")
    if not isinstance(rows, list):
        return {"verified": False, "reason": "event_list_missing"}
    matches = [row for row in rows if isinstance(row, dict)
               and str(row.get("source_event_id") or "") == str(source_event_id)]
    if len(matches) != 1:
        return {"verified": False, "reason": "source_event_missing_or_ambiguous"}
    event = matches[0]
    if event.get("is_live") is not True:
        return {"verified": False, "reason": "event_not_live"}
    raw_score = (event.get("score_state") or {}).get("raw_score_string") or event.get("score")
    if expected_score is not None and str(raw_score or "") != str(expected_score):
        return {"verified": False, "reason": "score_mismatch"}
    try:
        parsed_state = (state_parser or _parse_state)(event)
    except Exception:
        return {"verified": False, "reason": "state_reparse_failed"}
    if _without_timestamps(parsed_state) != _without_timestamps(expected_state):
        return {"verified": False, "reason": "state_mismatch"}

    evidence = {
        "snapshot_at": cutoff.isoformat(), "available_at": available_at.isoformat(),
        "source_event_id": str(source_event_id), "raw_object_key": raw_key,
        "raw_content_hash": digest, "collector_version": document.get("collector_version"),
        "parser_version": PARSER_VERSION,
    }
    evidence_hash = hashlib.sha256(json.dumps(
        evidence, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return {"verified": True, "reason": None, "available_at": available_at,
            "raw_content_hash": digest, "evidence_hash": evidence_hash,
            "evidence_json": evidence, "parser_version": PARSER_VERSION}
