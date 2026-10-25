"""Rebuild normalized API-Tennis matches from the compressed MinIO raw archive.

This command needs database and MinIO credentials but does not need API-Tennis access.
Run in the external-tennis-sources image: python /app/scripts/import_api_tennis_archive.py
"""

import gzip
import hashlib
import json
import logging
import re
import uuid
import zlib
from datetime import date, datetime

from sqlalchemy import text

from app.external_tennis_sources import SourceArchive
from app.storage import ResearchStorage


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
    events = envelope.get("result")
    if envelope.get("success") not in (1, True) or not isinstance(events, list):
        return None
    return observed_day, body, events, digest


def main():
    logging.basicConfig(level=logging.INFO)
    storage = ResearchStorage()
    client = storage._get_client()
    if not client:
        raise SystemExit("MinIO is unavailable")
    archive = SourceArchive()
    paginator = client.get_paginator("list_objects_v2")
    restored = 0
    for page in paginator.paginate(Bucket=storage.bucket, Prefix="snapshots/external-tennis/api-tennis/"):
        for item in page.get("Contents", []):
            key = item["Key"]
            try:
                raw_object = client.get_object(Bucket=storage.bucket, Key=key)
                decoded = decode_archive_object(key, raw_object["Body"].read())
                if decoded is None:
                    logging.error("Skipping invalid or hash-mismatched archive object key=%s", key)
                    continue
                observed_day, body, events, digest = decoded
            except (OSError, ValueError, TypeError):
                logging.exception("Skipping unreadable archive object key=%s", key)
                continue
            metadata = raw_object.get("Metadata") or {}
            fetched_at = datetime.fromisoformat(metadata["fetched-at"]) if metadata.get("fetched-at") else None
            archive.persist_events({"id": uuid.uuid4(), "interval_start": observed_day,
                                    "response_bytes": len(body), "observed_at": fetched_at}, events, key, digest)
            restored += len(events)
    print(f"Restored records: {restored}")


if __name__ == "__main__":
    main()
