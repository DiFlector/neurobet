"""Offline restore must verify content-addressed MinIO objects."""

import gzip
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from import_api_tennis_archive import decode_archive_object, decode_event_types_object  # noqa: E402


def _key(body: bytes, digest: str | None = None) -> str:
    digest = digest or hashlib.sha256(body).hexdigest()
    return f"snapshots/external-tennis/api-tennis/2026-10-22/{digest}.json.gz"


def test_restore_accepts_valid_hash_and_provider_envelope():
    body = json.dumps({"success": 1, "result": [{"event_key": "event-1"}]}).encode()

    decoded = decode_archive_object(_key(body), gzip.compress(body))

    assert decoded is not None
    day, raw, events, digest = decoded
    assert day.isoformat() == "2026-10-22"
    assert raw == body
    assert events == [{"event_key": "event-1"}]
    assert digest == hashlib.sha256(body).hexdigest()


def test_restore_accepts_successful_provider_empty_response():
    body = json.dumps({"success": 1}).encode()

    decoded = decode_archive_object(_key(body), gzip.compress(body))

    assert decoded is not None
    assert decoded[2] == []


def test_restore_rejects_object_when_path_hash_does_not_match_content():
    body = json.dumps({"success": 1, "result": [{"event_key": "event-1"}]}).encode()

    assert decode_archive_object(_key(body, "0" * 64), gzip.compress(body)) is None


def test_restore_rejects_invalid_provider_envelope_and_key():
    body = json.dumps({"success": 1, "data": []}).encode()

    assert decode_archive_object(_key(body), gzip.compress(body)) is None
    assert decode_archive_object("snapshots/external-tennis/api-tennis/not-an-interval/object.gz",
                                gzip.compress(body)) is None
    assert decode_archive_object(_key(body), b"truncated-gzip") is None


def test_event_types_archive_is_hash_verified_and_normalized():
    body = json.dumps({"success": 1, "result": [
        {"event_type_key": 17, "event_type_type": "ATP Men Singles"},
        {"event_type_key": None, "event_type_type": "ignored"},
        {"event_type_key": 23},
    ]}).encode()
    digest = hashlib.sha256(body).hexdigest()
    key = f"snapshots/external-tennis/api-tennis/event-types/{digest}.json.gz"

    decoded = decode_event_types_object(key, gzip.compress(body))

    assert decoded == (body, [{"key": "17", "name": "ATP Men Singles"},
                              {"key": "23", "name": ""}], digest)
    assert decode_event_types_object(key.replace(digest, "0" * 64), gzip.compress(body)) is None
