"""Rebuild normalized API-Tennis matches from the compressed MinIO raw archive.

This command needs database and MinIO credentials but does not need API-Tennis access.
Run in the external-tennis-sources image: python /app/scripts/import_api_tennis_archive.py
"""

import gzip
import argparse
import hashlib
import json
import logging
import re
import uuid
import zlib
from datetime import UTC, date, datetime

from sqlalchemy import text

from app.external_tennis_sources import SourceArchive
from app.storage import ResearchStorage

_EVENT_TYPES_PREFIX = "snapshots/external-tennis/api-tennis/event-types/"


def decode_event_types_object(key: str, compressed_body: bytes):
    """Verify and decode API-Tennis' archived event-type catalog object."""
    match = re.fullmatch(r"snapshots/external-tennis/api-tennis/event-types/([0-9a-f]{64})\.json\.gz", key)
    if not match:
        return None
    try:
        body = gzip.decompress(compressed_body)
        envelope = json.loads(body)
    except (OSError, EOFError, ValueError, TypeError, zlib.error):
        return None
    digest = hashlib.sha256(body).hexdigest()
    if digest != match.group(1) or not isinstance(envelope, dict):
        return None
    if envelope.get("success") not in (1, True) or not isinstance(envelope.get("result"), list):
        return None
    event_types = [
        {"key": str(item.get("event_type_key")), "name": str(item.get("event_type_type") or "")}
        for item in envelope["result"]
        if isinstance(item, dict) and item.get("event_type_key") is not None
    ]
    return body, event_types, digest


def restore_event_types(archive: SourceArchive, *, key: str, digest: str,
                        event_types: list[dict], observed_at: datetime | None) -> None:
    """Restore the catalog's latest source-state projection idempotently."""
    payload = json.dumps({
        "observed_at": (observed_at or datetime.now(UTC)).isoformat(),
        "raw_object_key": key,
        "content_sha256": digest,
        "event_types": event_types,
    }, ensure_ascii=False)
    with archive.engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
            VALUES ('api_tennis','event_types',CAST(:payload AS jsonb),now())
            ON CONFLICT(source,state_key) DO UPDATE
            SET state_json=excluded.state_json,updated_at=excluded.updated_at
        """), {"payload": payload})


def decode_archive_object(key: str, compressed_body: bytes):
    """Verify the content-addressed key and validate one saved provider response."""
    match = re.search(r"/([0-9]{4}-[0-9]{2}-[0-9]{2})/([0-9a-f]{64})\.json\.gz$", key)
    if not match:
        return None
    try:
        body = gzip.decompress(compressed_body)
        envelope = json.loads(body)
        observed_day = date.fromisoformat(match.group(1))
    except (OSError, EOFError, ValueError, TypeError, zlib.error):
        return None
    if not isinstance(envelope, dict):
        return None
    digest = hashlib.sha256(body).hexdigest()
    if digest != match.group(2):
        return None
    if envelope.get("success") not in (1, True):
        return None
    # API-Tennis represents an empty interval as {"success": 1} (without a
    # result field). Preserve that as a valid, empty response during offline
    # recovery instead of reporting a healthy archive object as corrupt.
    if "result" not in envelope and set(envelope) != {"success"}:
        return None
    events = envelope.get("result", [])
    if not isinstance(events, list):
        return None
    return observed_day, body, events, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-canonical-projection", action="store_true",
        help="Restore source observations/results/PBP without the separate canonical identity projection.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    storage = ResearchStorage()
    client = storage._get_client()
    if not client:
        raise SystemExit("MinIO is unavailable")
    archive = SourceArchive()
    paginator = client.get_paginator("list_objects_v2")
    restored = 0
    empty = 0
    invalid = 0
    catalogs = 0
    objects = 0
    for page in paginator.paginate(Bucket=storage.bucket, Prefix="snapshots/external-tennis/api-tennis/"):
        for item in page.get("Contents", []):
            key = item["Key"]
            objects += 1
            try:
                raw_object = client.get_object(Bucket=storage.bucket, Key=key)
                compressed_body = raw_object["Body"].read()
                if key.startswith(_EVENT_TYPES_PREFIX):
                    catalog = decode_event_types_object(key, compressed_body)
                    if catalog is None:
                        logging.error("Skipping invalid API-Tennis event catalog key=%s", key)
                        invalid += 1
                        continue
                    _body, event_types, digest = catalog
                    metadata = raw_object.get("Metadata") or {}
                    fetched_at = datetime.fromisoformat(metadata["fetched-at"]) if metadata.get("fetched-at") else None
                    restore_event_types(archive, key=key, digest=digest, event_types=event_types,
                                        observed_at=fetched_at)
                    catalogs += 1
                    continue
                decoded = decode_archive_object(key, compressed_body)
                if decoded is None:
                    logging.error("Skipping invalid or hash-mismatched archive object key=%s", key)
                    invalid += 1
                    continue
                observed_day, body, events, digest = decoded
            except (OSError, ValueError, TypeError):
                logging.exception("Skipping unreadable archive object key=%s", key)
                continue
            metadata = raw_object.get("Metadata") or {}
            fetched_at = datetime.fromisoformat(metadata["fetched-at"]) if metadata.get("fetched-at") else None
            if events:
                archive.persist_events({"id": uuid.uuid4(), "interval_start": observed_day,
                                        "response_bytes": len(body), "observed_at": fetched_at}, events, key, digest,
                                      project_canonical=not args.skip_canonical_projection)
                restored += len(events)
            else:
                empty += 1
    print(f"Objects scanned: {objects}; restored records: {restored}; valid empty responses: {empty}; "
          f"catalogs restored: {catalogs}; invalid objects: {invalid}")


if __name__ == "__main__":
    main()
