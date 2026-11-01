"""Recover timestamped final-score witnesses from existing Fonbet snapshots.

This read-only job writes exact database keys for an independent label audit.
It never changes snapshot times, event metadata, identities or history links.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import signal
import uuid

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from db.connection import SessionLocal
from tennis_adapter.history_linking import official_result
from tennis_result_observation import score_signature


def _utc(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("witness_timestamp_requires_timezone")
    return parsed.astimezone(timezone.utc)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _atomic(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    temporary.replace(path)


def _verify_witness(candidate, row, cutoff):
    if str(row["event_id"]) != candidate["id"] or str(row["source_event_id"]) != candidate["source_event_id"]:
        return "snapshot_event_identity_mismatch"
    if not (_utc(candidate["lower_bound"]) <= _utc(row["observed_at"]) <= _utc(cutoff)):
        return "snapshot_outside_fixed_time_bounds"
    if score_signature(row.get("score")) != score_signature(candidate["final_score"]):
        return "snapshot_final_score_mismatch"
    return None


def _load_candidates(ids, cutoff, timeout):
    candidates, excluded = [], []
    with SessionLocal() as session:
        session.execute(text(f"SET LOCAL statement_timeout='{timeout}s'"))
        for start in range(0, len(ids), 100):
            rows = session.execute(text("""
                SELECT id,source_event_id,status,first_seen_at,created_at,
                       metadata_json->'fonbet_official_result' AS result,
                       metadata_json->'set_winners' AS set_winners
                FROM events WHERE id=ANY(CAST(:ids AS uuid[]))
                  AND source='fonbet' AND sport_code='tennis' AND created_at<=:cutoff
                ORDER BY id
            """), {"ids": ids[start:start+100], "cutoff": cutoff}).mappings()
            for row in rows:
                metadata = {"fonbet_official_result": row["result"], "set_winners": row["set_winners"]}
                winner, _sets, reason = official_result(row["status"], metadata)
                base = {"id": str(row["id"]), "source_event_id": str(row["source_event_id"])}
                if winner not in (0, 1):
                    excluded.append({**base, "reason": reason})
                    continue
                result = row["result"]
                raw_time = result.get("available_at") or result.get("observed_at")
                try:
                    metadata_time = _utc(raw_time).isoformat() if raw_time else None
                except (TypeError, ValueError):
                    metadata_time = None
                candidates.append({**base, "final_score": result["final_score"], "winner": winner,
                    "lower_bound": _utc(row["first_seen_at"] or row["created_at"]).isoformat(),
                    "metadata_time": metadata_time})
    return sorted(candidates, key=lambda row: row["id"]), excluded


def _query(candidates, cutoff, timeout, *, history=False):
    # A latest-row index lookup handles the common case without reading JSONB.
    # Only mismatched latest rows require the bounded historical score search.
    predicate = "AND score ~ requested.pattern" if history else ""
    with SessionLocal() as session:
        session.execute(text(f"SET LOCAL statement_timeout='{timeout}s'"))
        return list(session.execute(text("""
            WITH requested AS (
                SELECT * FROM unnest(CAST(:ids AS uuid[]), CAST(:lower AS timestamptz[]),
                                     CAST(:upper AS timestamptz[]), CAST(:patterns AS text[]))
                AS r(event_id,lower_bound,upper_bound,pattern)
            )
            SELECT snapshot.* FROM requested JOIN LATERAL (
                SELECT observed_at
                FROM event_state_snapshots
                WHERE event_id=requested.event_id AND sport_code='tennis'
                  AND observed_at>=:global_lower AND observed_at<=:global_upper
                  AND observed_at>=requested.lower_bound AND observed_at<=requested.upper_bound
            """ + predicate + """
                ORDER BY observed_at DESC LIMIT 1
            ) chosen ON TRUE
            JOIN LATERAL (
                SELECT id AS snapshot_id,event_id,source_event_id,observed_at,score,status,
                       raw_hash,raw_object_key
                FROM event_state_snapshots
                WHERE event_id=requested.event_id AND observed_at=chosen.observed_at
                  AND observed_at>=:global_lower AND observed_at<=:global_upper
                  AND sport_code='tennis'
            """ + predicate + """
                ORDER BY id DESC LIMIT 1
            ) snapshot ON TRUE
        """), {"ids": [uuid.UUID(c["id"]) for c in candidates],
                "lower": [_utc(c["lower_bound"]) for c in candidates],
                "global_lower": min(_utc(c["lower_bound"]) for c in candidates),
                "global_upper": max(min(_utc(c["metadata_time"]), cutoff) if c["metadata_time"] else cutoff
                                    for c in candidates),
                "upper": [min(_utc(c["metadata_time"]), cutoff) if c["metadata_time"] else cutoff for c in candidates],
                "patterns": [r"^[^0-9]*" + r"[^0-9]+".join(map(str, score_signature(c["final_score"]))) + r"[^0-9]*$"
                             for c in candidates]}).mappings())


def _bounded_query(candidates, cutoff, timeout, *, history=False):
    try:
        return _query(candidates, cutoff, timeout, history=history)
    except SQLAlchemyError as exc:
        if len(candidates) <= 1 or "statement timeout" not in str(exc):
            raise
        middle = len(candidates)//2
        return (_bounded_query(candidates[:middle], cutoff, timeout, history=history)
                + _bounded_query(candidates[middle:], cutoff, timeout, history=history))


def recover(index_path, output_dir, cutoff, *, resume=False, batch_size=20, timeout=30, runtime=900):
    if not 1 <= batch_size <= 100 or not 1 <= timeout <= 30 or not 1 <= runtime <= 900:
        raise ValueError("recovery_bounds_exceeded")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    raw = Path(index_path).read_bytes()
    settings = {"index_sha256": hashlib.sha256(raw).hexdigest(), "cutoff": cutoff.isoformat(),
                "batch_size": batch_size, "query_timeout_seconds": timeout}
    checkpoint_path = root / "checkpoint.json"
    if checkpoint_path.exists():
        if not resume:
            raise ValueError("checkpoint_exists_pass_resume")
        checkpoint = json.loads(checkpoint_path.read_text())
        if checkpoint["version"] != 1 or checkpoint["settings"] != settings:
            raise ValueError("checkpoint_input_or_settings_changed")
        if checkpoint["candidates_sha256"] != _digest(checkpoint["candidates"]):
            raise ValueError("checkpoint_candidate_digest_mismatch")
        if checkpoint["rows_sha256"] != _digest(checkpoint["rows"]):
            raise ValueError("checkpoint_evidence_digest_mismatch")
    else:
        if resume:
            raise ValueError("checkpoint_missing")
        ids = sorted({uuid.UUID(json.loads(line)["fonbet_event_id"]) for line in raw.splitlines() if line.strip()})
        candidates, excluded = _load_candidates(ids, cutoff, timeout)
        checkpoint = {"version": 1, "settings": settings, "indexed_events": len(ids),
                      "candidates": candidates, "excluded": excluded, "rows": []}
        checkpoint.update(candidates_sha256=_digest(candidates), rows_sha256=_digest([]))
        _atomic(checkpoint_path, checkpoint)
    failure = None
    def expire(_signum, _frame):
        raise TimeoutError("recovery_runtime_budget_exceeded")
    previous_handler = signal.signal(signal.SIGALRM, expire)
    signal.alarm(runtime)
    try:
        for start in range(len(checkpoint["rows"]), len(checkpoint["candidates"]), batch_size):
            batch = checkpoint["candidates"][start:start+batch_size]
            found = {}
            for offset in range(0, len(batch), 5):
                group = batch[offset:offset+5]
                latest = {str(row["event_id"]): dict(row) for row in _bounded_query(group, cutoff, timeout)}
                unresolved = [c for c in group if c["id"] in latest and _verify_witness(c, latest[c["id"]], cutoff)]
                if unresolved:
                    for row in _bounded_query(unresolved, cutoff, timeout, history=True):
                        latest[str(row["event_id"])] = dict(row)
                found.update(latest)
            completed = []
            for candidate in batch:
                row = found.get(candidate["id"])
                reason = _verify_witness(candidate, row, cutoff) if row else "no_snapshot_with_confirmed_final_score"
                if reason:
                    completed.append({**candidate, "reason": reason})
                    continue
                at = _utc(row["observed_at"])
                completed.append({**candidate, "reason": "confirmed_saved_final_score",
                    "earlier_than_metadata": bool(candidate["metadata_time"] and at<_utc(candidate["metadata_time"])),
                    "restored_unknown_time": candidate["metadata_time"] is None,
                    "witness": {"fonbet_event_id": candidate["id"], "fonbet_source_event_id": candidate["source_event_id"],
                        "snapshot_id": str(row["snapshot_id"]), "snapshot_observed_at": at.isoformat(),
                        "score": row["score"], "status": row["status"], "raw_hash": row["raw_hash"],
                        "raw_object_key": row["raw_object_key"], "recovered_at": datetime.now(timezone.utc).isoformat()}})
            checkpoint["rows"].extend(completed)
            checkpoint["rows_sha256"] = _digest(checkpoint["rows"])
            _atomic(checkpoint_path, checkpoint)
            print(json.dumps({"completed": len(checkpoint["rows"]), "candidates": len(checkpoint["candidates"]),
                "witnesses": sum("witness" in r for r in checkpoint["rows"])}), flush=True)
    except (TimeoutError, SQLAlchemyError) as exc:
        failure = ("statement_timeout" if "statement timeout" in str(exc) else type(exc).__name__)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
    witnesses = [r["witness"] for r in checkpoint["rows"] if "witness" in r]
    witness_path = root / "result_witness_index.jsonl"
    temporary = witness_path.with_suffix(".jsonl.tmp")
    temporary.write_text("".join(json.dumps(w, ensure_ascii=False, sort_keys=True)+"\n" for w in witnesses))
    temporary.replace(witness_path)
    complete = len(checkpoint["rows"]) == len(checkpoint["candidates"]) and failure is None
    report = {"format_version": 1, "status": "complete" if complete else "incomplete", "error": failure,
        "scope": "saved_cohort_result_time_recovery_only", "cutoff": cutoff.isoformat(),
        "indexed_events": checkpoint["indexed_events"], "valid_result_candidates": len(checkpoint["candidates"]),
        "completed_events": len(checkpoint["rows"]), "witness_events": len(witnesses),
        "earlier_than_metadata": sum(bool(r.get("earlier_than_metadata")) for r in checkpoint["rows"]),
        "restored_unknown_time": sum(bool(r.get("restored_unknown_time")) for r in checkpoint["rows"]),
        "reason_counts": dict(Counter(r["reason"] for r in checkpoint["rows"]+checkpoint["excluded"])),
        "witness_index": {"path": str(witness_path), "sha256": hashlib.sha256(witness_path.read_bytes()).hexdigest()},
        "training_gate": "blocked_recovery_is_not_admission"}
    _atomic(root / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-index", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cutoff", type=_utc, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--event-batch-size", type=int, default=20)
    parser.add_argument("--query-timeout-seconds", type=int, default=30)
    parser.add_argument("--max-runtime-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.cutoff > datetime.now(timezone.utc):
        parser.error("cutoff_cannot_be_in_future")
    report = recover(args.snapshot_index, args.output_dir, args.cutoff, resume=args.resume,
                     batch_size=args.event_batch_size, timeout=args.query_timeout_seconds,
                     runtime=args.max_runtime_seconds)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if report["status"] == "complete" else 2)


if __name__ == "__main__":
    main()
