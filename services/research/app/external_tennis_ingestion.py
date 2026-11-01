"""Shared, immutable envelope for external tennis observations.

Provider adapters remain responsible for parsing their source format. This
module makes source identity, parse status, raw-document linkage and field-level
provenance consistent before observations are projected into canonical tables.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable


def build_observation_envelope(
    *,
    source: str,
    external_id: str,
    source_url: str,
    parser_version: str,
    available_at: datetime,
    source_content_hash: str | None,
    source_document_ref: str | None,
    raw_fields: dict[str, Any],
    normalized_fields: dict[str, Any],
    required_fields: Iterable[str] = (),
    derived_fields: Iterable[str] = (),
    field_sources: dict[str, str] | None = None,
    hash_source_content: bool = True,
    retrospective: bool = True,
) -> dict[str, Any]:
    """Build deterministic parse metadata; same document/parser is idempotent."""
    required = set(required_fields)
    derived = set(derived_fields)
    provenance: dict[str, dict[str, Any]] = {}
    for name, value in sorted(normalized_fields.items()):
        raw_path = (field_sources or {}).get(name, name)
        raw_value = raw_fields.get(raw_path)
        status = "derived" if name in derived else (
            "observed" if value is not None and (raw_value is not None or raw_path in raw_fields)
            else "missing"
        )
        raw_digest = hashlib.sha256(
            json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest() if value is not None else None
        provenance[name] = {
            "status": status,
            "required": name in required,
            "source": source,
            "source_url": source_url,
            "source_field": raw_path if raw_value is not None or raw_path in raw_fields else None,
            "parser_version": parser_version,
            "available_at": available_at.isoformat(),
            "source_document_ref": source_document_ref,
            "value_sha256": raw_digest,
        }

    has_invalid = any(item["status"] == "invalid" for item in provenance.values())
    has_missing = any(item["status"] == "missing" for item in provenance.values())
    status = "rejected" if has_invalid else ("partial" if has_missing else "parsed")
    stable = {
        "source": source,
        "external_id": external_id,
        "source_content_hash": source_content_hash if hash_source_content else None,
        "parser_version": parser_version,
        "normalized_fields": normalized_fields,
    }
    observation_hash = hashlib.sha256(
        json.dumps(stable, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    return {
        "observation_hash": observation_hash,
        "ingestion_status": status,
        "source_document_ref": source_document_ref,
        "field_provenance": provenance,
        "observation_metadata": {
            "source": source,
            "external_id": external_id,
            "source_url": source_url,
            "source_content_hash": source_content_hash,
            "source_document_ref": source_document_ref,
            "parser_version": parser_version,
            "available_at": available_at.isoformat(),
            "ingestion_status": status,
            "retrospective": retrospective,
            "field_provenance": provenance,
        },
    }
