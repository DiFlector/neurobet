from datetime import datetime, timedelta, timezone
import sys
from types import SimpleNamespace
from uuid import UUID

sys.path.insert(0, "/srv/neurobet/scripts/research")
from tennis_label_audit import _utc_result_time, _walk_forward_admission, _window_summary


def test_window_minimum_counts_only_matches_with_target_labels():
    ids = [UUID(int=value) for value in range(1, 11)]
    labels = {ids[0]: {0}, ids[1]: {1}}
    summary = _window_summary(ids, {}, labels, required=3)
    assert summary["candidate_matches"] == 10
    assert summary["matches"] == 2
    assert summary["class_a"] == 1
    assert summary["class_b"] == 1
    assert summary["both_classes"] is True
    assert summary["meets_match_minimum"] is False


def test_scheduled_start_is_not_used_as_result_availability():
    event = SimpleNamespace(
        metadata_json={"fonbet_official_result": {}},
        scheduled_start_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    assert _utc_result_time(event) is None


def test_official_result_availability_uses_persisted_source_observation():
    available_at = "2026-10-20T12:34:56+00:00"
    event = SimpleNamespace(metadata_json={"fonbet_official_result": {"available_at": available_at}})
    assert _utc_result_time(event) == datetime.fromisoformat(available_at)


def test_walk_forward_split_is_shared_and_excludes_undated_matches(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.json"
    monkeypatch.setenv("TENNIS_TRAINING_MANIFEST_PATH", str(manifest_path))
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    ids = [UUID(int=value) for value in range(1, 31)]
    event_times = {event_id: start + timedelta(days=index) for index, event_id in enumerate(ids)}
    event_times.pop(ids[-1])
    rows = {
        "point": {(event_id, index % 2) for index, event_id in enumerate(ids[:10])},
        "game": {(event_id, index % 2) for index, event_id in enumerate(ids[:20])},
        "set": {(event_id, index % 2) for index, event_id in enumerate(ids[:25])},
        "match": {(event_id, index % 2) for index, event_id in enumerate(ids[:29])},
    }
    report, metadata = _walk_forward_admission(rows, event_times)
    assert metadata["matches"] == 29
    assert metadata["holdout_quality_metrics_opened"] is False
    for target in rows:
        assert report[target]["manifest_matches"] == 29
        assert report[target]["folds"][0]["train"]["candidate_matches"] == report["point"]["folds"][0]["train"]["candidate_matches"]
    assert ids[-1] not in [UUID(value) for value in __import__("json").loads(manifest_path.read_text())["ordered_event_ids"]]
