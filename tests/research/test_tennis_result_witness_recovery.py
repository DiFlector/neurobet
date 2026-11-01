from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "research"))
import recover_tennis_result_witnesses as recovery


def _candidate():
    return {"id": "00000000-0000-0000-0000-000000000001", "source_event_id": "12345",
            "final_score": "2:0 (6-4 6-3)", "lower_bound": "2026-10-13T08:00:00Z",
            "metadata_time": "2026-10-21T12:00:00Z", "winner": 0}


def _snapshot():
    c = _candidate()
    return {"event_id": c["id"], "source_event_id": c["source_event_id"],
            "observed_at": "2026-10-13T10:00:00Z", "score": "2-0 (6-4, 6-3)"}


def test_recovery_requires_exact_identity_full_score_and_observation_bounds():
    candidate, row = _candidate(), _snapshot()
    cutoff = recovery._utc("2026-10-28T05:25:39Z")
    assert recovery._verify_witness(candidate, row, cutoff) is None
    for key, value, expected in [
        ("event_id", "different-uuid", "snapshot_event_identity_mismatch"),
        ("source_event_id", "54321", "snapshot_event_identity_mismatch"),
        ("score", "2-0 (6-4, 6-2)", "snapshot_final_score_mismatch"),
        ("observed_at", "2026-10-12T10:00:00Z", "snapshot_outside_fixed_time_bounds"),
        ("observed_at", "2026-10-29T10:00:00Z", "snapshot_outside_fixed_time_bounds"),
    ]:
        assert recovery._verify_witness(candidate, {**row, key: value}, cutoff) == expected


def test_completed_recovery_resume_preserves_same_evidence_digest(tmp_path, monkeypatch):
    index = tmp_path / "input.jsonl"
    candidate = _candidate()
    index.write_text(json.dumps({"fonbet_event_id": candidate["id"]})+"\n")
    output = tmp_path / "recovery"
    output.mkdir()
    cutoff = recovery._utc("2026-10-28T05:25:39Z")
    witness = {"fonbet_event_id": candidate["id"], "snapshot_id": "s1",
               "snapshot_observed_at": "2026-10-13T10:00:00Z", "recovered_at": "2026-10-28T07:00:00Z"}
    rows = [{**candidate, "reason": "confirmed_saved_final_score", "witness": witness,
             "earlier_than_metadata": True}]
    checkpoint = {"version": 1, "settings": {"index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
         "cutoff": cutoff.isoformat(), "batch_size": 20, "query_timeout_seconds": 30},
         "indexed_events": 1, "candidates": [candidate], "excluded": [], "rows": rows,
         "candidates_sha256": recovery._digest([candidate]), "rows_sha256": recovery._digest(rows)}
    (output / "checkpoint.json").write_text(json.dumps(checkpoint))
    def unexpected(*args, **kwargs):
        raise AssertionError("a completed checkpoint must not reread historical data")
    monkeypatch.setattr(recovery, "_query", unexpected)
    first = recovery.recover(str(index), str(output), cutoff, resume=True)
    second = recovery.recover(str(index), str(output), cutoff, resume=True)
    assert first == second
    assert first["status"] == "complete"
    assert first["witness_events"] == 1
    assert first["earlier_than_metadata"] == 1
    # Tampered checkpoint evidence cannot silently replace the saved witness.
    checkpoint["rows"][0]["witness"]["snapshot_observed_at"] = "2026-10-12T00:00:00Z"
    (output / "checkpoint.json").write_text(json.dumps(checkpoint))
    import pytest
    with pytest.raises(ValueError, match="evidence_digest_mismatch"):
        recovery.recover(str(index), str(output), cutoff, resume=True)


def test_timeout_retries_every_candidate_in_smaller_queries(monkeypatch):
    from sqlalchemy.exc import OperationalError
    calls = []
    def query(candidates, cutoff, timeout, *, history):
        calls.append([c['id'] for c in candidates])
        if len(candidates)>1:
            raise OperationalError('read', {}, Exception('canceling statement due to statement timeout'))
        return candidates
    monkeypatch.setattr(recovery, '_query', query)
    candidates = [{'id': str(i)} for i in range(5)]
    assert recovery._bounded_query(candidates, None, 30, history=True) == candidates
    assert [c[0] for c in calls if len(c)==1] == [str(i) for i in range(5)]
