from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "research"))

from tennis_history_coverage import (_label_maps, _one_label, _write_witness_cohort,
                                    _bounded_read, _write_expanded_cohort)


def test_coverage_uses_confirmed_game_start_and_conservative_label_time():
    at = datetime(2026, 10, 28, tzinfo=timezone.utc)
    rows = [
        {"set": 1, "game_score": [1, 0], "state_before": ["30", "0"],
         "server": 0, "winner": 0, "confirmed": True, "quality": "confirmed"},
        {"set": 1, "game_score": [1, 0], "state_after": {"game_result": [1, 0]},
         "winner": 0, "confirmed": True, "quality": "terminal_from_game_result"},
    ]
    version = SimpleNamespace(id="v1", document_id="d1", available_at=at,
                              parser_version="flashscore-point-history-v7",
                              events_json=rows, quality_json={"set_number": 1})
    points, games, sets = _label_maps([version], True, [(6, 4)], at + timedelta(seconds=1), at)
    assert sets == 1
    assert set(points) == {(1, (0, 0), (2, 0), False)}
    label = _one_label(games[(1, (0, 0))], at - timedelta(seconds=1))
    assert label["label"] == 0
    assert label["label_available_at"] == (at + timedelta(seconds=1)).isoformat()
    assert _one_label([label], at + timedelta(seconds=2)) is None
    assert _one_label([label, {**label, "label": 1}], at) is None


def test_empty_correction_replaces_older_source_set():
    at = datetime(2026, 10, 28, tzinfo=timezone.utc)
    correction = SimpleNamespace(id="v2", document_id="d1", available_at=at,
                                 parser_version="flashscore-point-history-v7", events_json=[],
                                 quality_json={"set_number": 1, "explicit_empty_correction": True})
    points, games, sets = _label_maps([correction], True, [(6, 4)], at, at)
    assert sets == 1
    assert not points and not games


def test_witness_cohort_keeps_tape_and_requires_complete_state(tmp_path):
    import json
    tape = tmp_path / "tape.jsonl"
    rows = [
        {"fonbet_event_id": "e1", "snapshot_id": "bad", "complete_point_state": False,
         "candidate_labels": {"game": {"label": 1}}},
        {"fonbet_event_id": "e1", "snapshot_id": "good", "complete_point_state": True,
         "candidate_labels": {"game": {"label": 1}, "point": {"label": 0}}},
    ]
    original = "".join(json.dumps(row) + "\n" for row in rows)
    tape.write_text(original)
    result = _write_witness_cohort([{"fonbet_event_id": "e1", "index_path": str(tape)}], tmp_path)
    witnesses = [json.loads(line) for line in Path(result["path"]).read_text().splitlines()]
    assert witnesses == [{"fonbet_event_id": "e1"}, rows[1]]
    assert result["matches_with_complete_state_by_target"] == {"point": 1, "game": 1}
    assert tape.read_text() == original


def test_timeout_retries_smaller_page_and_other_database_errors_propagate():
    import pytest
    from sqlalchemy.exc import OperationalError
    attempted = []
    def loader(limit):
        attempted.append(limit)
        if limit > 125:
            raise OperationalError("read snapshots", {}, Exception("canceling statement due to statement timeout"))
        return ["saved-row"]
    assert _bounded_read(loader, 500) == (["saved-row"], 125, 2)
    assert attempted == [500, 250, 125]
    def failed(_):
        raise OperationalError("read snapshots", {}, Exception("connection lost"))
    with pytest.raises(OperationalError):
        _bounded_read(failed, 500)


def test_expanded_index_keeps_result_witnesses_and_rejects_identity_conflicts(tmp_path):
    import json
    import pytest
    baseline, witness = tmp_path / "baseline.jsonl", tmp_path / "witness.jsonl"
    baseline.write_text(json.dumps({"fonbet_event_id": "e1", "snapshot_id": "s1",
        "snapshot_observed_at": "2026-10-28T00:00:00Z", "result_time_snapshot_evidence": {
            "snapshot_id": "result", "observed_at": "2026-10-28T01:00:00Z"}}) + "\n")
    witness.write_text(json.dumps({"fonbet_event_id": "e1", "snapshot_id": "s2",
                                   "snapshot_observed_at": "2026-10-28T00:30:00Z"}) + "\n")
    result = _write_expanded_cohort(baseline, witness, tmp_path)
    assert (result["baseline_snapshot_keys"], result["snapshot_keys"], result["added_snapshot_keys"]) == (2, 3, 1)
    rows = [json.loads(line) for line in Path(result["path"]).read_text().splitlines()]
    assert {row["snapshot_id"] for row in rows if "snapshot_id" in row} == {"s1", "s2", "result"}
    witness.write_text(json.dumps({"fonbet_event_id": "wrong-event", "snapshot_id": "s1",
                                   "snapshot_observed_at": "2026-10-28T00:00:00+00:00"}) + "\n")
    with pytest.raises(ValueError, match="snapshot_index_conflicting_event_id"):
        _write_expanded_cohort(baseline, witness, tmp_path)
