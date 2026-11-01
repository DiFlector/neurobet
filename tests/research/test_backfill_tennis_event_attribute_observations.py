import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "research"))
from backfill_tennis_event_attribute_observations import _candidates


def test_legacy_attribute_backfill_preserves_source_time_and_hash():
    metadata = {
        "best_of": 3,
        "best_of_available_at": "2026-10-20T10:00:00Z",
        "best_of_source": "official_catalog",
        "best_of_source_match_id": "catalog-7",
        "best_of_source_content_sha256": "abc123",
        "best_of_source_raw_object_key": "raw/catalog/7.json",
    }

    first = list(_candidates(metadata, "event-1"))
    repeated = list(_candidates(metadata, "event-1"))

    assert len(first) == 1
    assert first[0]["available_at"] == datetime(2026, 10, 20, 10, tzinfo=timezone.utc)
    assert first[0]["raw_key"] == "raw/catalog/7.json"
    assert first[0]["digest"] == repeated[0]["digest"]


def test_late_identity_moves_fact_time_forward_and_unstamped_values_are_ignored():
    metadata = {
        "surface": "clay",
        "surface_available_at": "2026-10-20T10:00:00Z",
        "surface_source": "live_tennis_api",
        "surface_identity_available_at": "2026-10-21T12:00:00Z",
        "best_of": 3,
        "best_of_source": "result_score",
    }

    rows = list(_candidates(metadata, "event-2"))

    assert len(rows) == 1
    assert rows[0]["attribute"] == "surface"
    assert rows[0]["available_at"] == datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
