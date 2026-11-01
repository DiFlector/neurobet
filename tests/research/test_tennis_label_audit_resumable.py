import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts" / "research"))

from tennis_label_audit import _write_training_examples
from tennis_label_audit_resumable import (
    _completed_batch_is_valid,
    _example_sort_key,
    _sort_example_files,
    _write_merged_training_examples,
    run_resumable,
)


def _row(target, event_id, snapshot_id, label):
    return {"target": target, "fonbet_event_id": event_id,
            "snapshot_id": snapshot_id, "label": label,
            "snapshot_observed_at": "2026-10-20T12:00:00+00:00",
            "label_available_at": "2026-10-20T12:05:00+00:00"}


def test_shard_merge_has_same_digest_as_continuous_order(tmp_path):
    rows = [
        _row("point", "00000000-0000-0000-0000-000000000003", "s3", 1),
        _row("match", "00000000-0000-0000-0000-000000000001", "s1", 0),
        _row("game", "00000000-0000-0000-0000-000000000002", "s2", 1),
    ]
    continuous = _write_training_examples(sorted(rows, key=_example_sort_key),
                                          str(tmp_path / "continuous"))
    left = tmp_path / "left.jsonl"
    right = tmp_path / "right.jsonl"
    left.write_text(json.dumps(rows[0]) + "\n" + json.dumps(rows[1]) + "\n", encoding="utf-8")
    right.write_text(json.dumps(rows[2]) + "\n", encoding="utf-8")

    sorted_paths = _sort_example_files([left, right], tmp_path / "merge" / "sorted_batches")
    merged = _write_merged_training_examples(sorted_paths, tmp_path / "merge")

    assert merged["examples"] == continuous["examples"] == 3
    assert merged["logical_digest"] == continuous["logical_digest"]
    assert merged["sha256"] == continuous["sha256"]


def test_completed_checkpoint_batch_rejects_modified_artifacts(tmp_path):
    artifact = tmp_path / "batch.jsonl"
    artifact.write_text("ok\n", encoding="utf-8")
    import hashlib
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    batch = {"index_path": "batch.jsonl", "index_sha256": digest,
             "report_path": "batch.jsonl", "report_sha256": digest,
             "examples_path": "batch.jsonl", "examples_sha256": digest,
             "diagnostics_path": "batch.jsonl", "diagnostics_sha256": digest}
    assert _completed_batch_is_valid(batch, tmp_path)

    artifact.write_text("changed\n", encoding="utf-8")
    assert not _completed_batch_is_valid(batch, tmp_path)


def test_old_alignment_checkpoint_cannot_mix_labels_with_repaired_alignment(tmp_path):
    (tmp_path / "checkpoint.json").write_text(json.dumps({"version": 5}))
    with pytest.raises(ValueError, match="checkpoint_version_mismatch"):
        run_resumable(output_path=str(tmp_path / "report.json"), checkpoint_dir=str(tmp_path),
                      cutoff=datetime(2026, 10, 28, tzinfo=timezone.utc), resume=True)


def test_verified_fingerprint_detects_same_size_change_even_if_mtime_restored(tmp_path):
    import hashlib
    import os
    artifact=tmp_path/'batch.jsonl'
    artifact.write_bytes(b'abc')
    digest=hashlib.sha256(b'abc').hexdigest()
    batch={}
    for name in ('index','report','examples','diagnostics'):
        batch[name+'_path']='batch.jsonl'; batch[name+'_sha256']=digest
    assert _completed_batch_is_valid(batch,tmp_path)
    saved=artifact.stat()
    artifact.write_bytes(b'xyz')
    os.utime(artifact,ns=(saved.st_atime_ns,saved.st_mtime_ns))
    assert not _completed_batch_is_valid(batch,tmp_path)


def test_merge_many_shards_stays_below_open_file_limit(tmp_path):
    rows=[_row('point',f'{n:036d}',f's{n}',n%2) for n in range(80,0,-1)]
    paths=[]
    for i,row in enumerate(rows):
        path=tmp_path/f'shard-{i}.jsonl'
        path.write_text(json.dumps(row)+'\n')
        paths.append(path)
    sorted_paths=_sort_example_files(paths,tmp_path/'merged'/'sorted')
    assert len(sorted_paths)<=64
    actual=_write_merged_training_examples(sorted_paths,tmp_path/'merged')
    expected=_write_training_examples(sorted(rows,key=_example_sort_key),str(tmp_path/'continuous'))
    assert actual['examples']==80
    assert actual['logical_digest']==expected['logical_digest']
    assert actual['sha256']==expected['sha256']


def test_resumable_merge_and_scan_preserve_digest_and_membership(tmp_path,monkeypatch):
    import tennis_label_audit_resumable as audit
    rows=[_row('point',f'{n:036d}',f's{n}',n%2) for n in range(10001)]
    sources=[]
    for group in range(3):
        path=tmp_path/f'source{group}.jsonl'
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows[group::3]))
        sources.append(path)
    sorted_paths=_sort_example_files(sources,tmp_path/'sorted')
    original=audit._finalization_progress
    def interrupt(path,stage,done):
        if stage.startswith('stream_') and done==10000: raise TimeoutError('interrupt')
    monkeypatch.setattr(audit,'_finalization_progress',interrupt)
    with pytest.raises(TimeoutError): _write_merged_training_examples(sorted_paths,tmp_path/'merged')
    monkeypatch.setattr(audit,'_finalization_progress',original)
    result=_write_merged_training_examples(sorted_paths,tmp_path/'merged')
    expected=_write_training_examples(sorted(rows,key=_example_sort_key),str(tmp_path/'expected'))
    assert result['logical_digest']==expected['logical_digest']
    assert result['sha256']==expected['sha256']
    def interrupt_scan(path,stage,done):
        if stage=='scan_examples' and done>=5000: raise TimeoutError('interrupt')
    monkeypatch.setattr(audit,'_finalization_progress',interrupt_scan)
    scan_path=tmp_path/'scan.sqlite'
    with pytest.raises(TimeoutError): audit._scan_example_files(sorted_paths,scan_path)
    monkeypatch.setattr(audit,'_finalization_progress',original)
    records=audit._scan_example_files(sorted_paths,scan_path)
    assert len(records)==10001
    assert sum(len(row['labels']) for row in records)==10001
