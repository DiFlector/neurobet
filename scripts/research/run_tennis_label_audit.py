"""Run and persist a current tennis PIT/admission audit without model training."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
import signal
import sys
from sqlalchemy.exc import SQLAlchemyError

from tennis_label_audit import build_report


class AuditTimeBudgetExceeded(TimeoutError):
    pass


def _cutoff(value: str | None):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("cutoff_requires_timezone")
    if parsed > datetime.now(timezone.utc):
        raise argparse.ArgumentTypeError("cutoff_cannot_be_in_future")
    return parsed.astimezone(timezone.utc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="JSON report output path")
    parser.add_argument("--snapshot-index", help="Revalidate a saved example cohort by exact snapshot primary keys")
    parser.add_argument("--result-evidence-index", help="Exact DB snapshot keys used only to verify result receipt times")
    parser.add_argument("--examples-output-dir", default="docs/research", help="Directory for provenance and per-match diagnostics")
    parser.add_argument("--cutoff", type=_cutoff, help="Fixed ISO-8601 data cutoff for a reproducible audit")
    parser.add_argument("--event-batch-size", type=int, default=100)
    parser.add_argument("--snapshot-page-size", type=int, default=500)
    parser.add_argument("--query-timeout-seconds", type=int, default=30)
    parser.add_argument("--max-runtime-seconds", type=int, default=900)
    parser.add_argument("--discipline-scope", choices=("singles", "doubles"), default="singles",
                        help="Build an isolated singles or doubles audit/dataset report")
    parser.add_argument("--checkpoint-dir", help="Persist bounded full-database audit progress here")
    parser.add_argument("--resume", action="store_true", help="Resume the checkpoint at --checkpoint-dir")
    args = parser.parse_args()
    if args.result_evidence_index and (not args.snapshot_index or args.checkpoint_dir):
        parser.error("--result-evidence-index requires --snapshot-index and cannot use --checkpoint-dir")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    def timeout(_signum, _frame):
        raise AuditTimeBudgetExceeded("audit_runtime_budget_exceeded")

    if not 1 <= args.max_runtime_seconds <= 900:
        parser.error("--max-runtime-seconds must be in 1..900")
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(args.max_runtime_seconds)
    try:
        if args.checkpoint_dir:
            if args.snapshot_index:
                parser.error("--checkpoint-dir cannot be combined with --snapshot-index")
            if args.cutoff is None:
                parser.error("--checkpoint-dir requires a fixed --cutoff")
            from tennis_label_audit_resumable import run_resumable
            report = run_resumable(output_path=str(output), checkpoint_dir=args.checkpoint_dir,
                cutoff=args.cutoff, resume=args.resume, event_batch_size=args.event_batch_size,
                snapshot_page_size=args.snapshot_page_size,
                query_timeout_seconds=args.query_timeout_seconds,
                max_runtime_seconds=args.max_runtime_seconds,
                discipline_scope=args.discipline_scope)
            if report.get("audit_scope", {}).get("status") != "complete":
                print(json.dumps({"audit_scope": report.get("audit_scope"),
                                  "training_gate": report.get("training_gate")}, sort_keys=True))
                raise SystemExit(2)
        else:
            if args.resume:
                parser.error("--resume requires --checkpoint-dir")
            report = build_report(snapshot_index_path=args.snapshot_index,
                result_evidence_index_path=args.result_evidence_index,
                training_examples_output_dir=args.examples_output_dir,
                audit_cutoff=args.cutoff, discipline_scope=args.discipline_scope,
                event_batch_size=args.event_batch_size,
                snapshot_page_size=args.snapshot_page_size,
                query_timeout_seconds=args.query_timeout_seconds)
    except (AuditTimeBudgetExceeded, TimeoutError, SQLAlchemyError) as exc:
        failure_code = ("runtime_timeout" if isinstance(exc, (AuditTimeBudgetExceeded, TimeoutError))
                        else "query_timeout" if "QueryCanceled" in str(exc)
                        else "database_error")
        incomplete = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "audit_scope": {"mode": "saved_snapshot_cohort" if args.snapshot_index else "full_database",
                            "full_database_census": False, "status": "incomplete",
                            "as_of": args.cutoff.isoformat() if args.cutoff else None,
                            "reason": failure_code,
                            "checkpoint_path": (str(Path(args.checkpoint_dir) / "checkpoint.json")
                                if args.checkpoint_dir else None)},
            "training_gate": {"status": "blocked", "windows_evaluated": False,
                              "point_in_time_certified": False,
                              "reasons": ["audit_incomplete"]},
        }
        output.write_text(json.dumps(incomplete, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                          encoding="utf-8")
        print(json.dumps(incomplete, ensure_ascii=False, sort_keys=True))
        print(f"audit incomplete: {exc}", file=sys.stderr)
        raise SystemExit(2)
    finally:
        signal.alarm(0)
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(output)
    report["audit_report_manifest"] = {
        "path": str(output), "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "bytes": len(rendered.encode()),
    }
    print(json.dumps({
        "generated_at": report.get("generated_at"),
        "audit_scope": report.get("audit_scope"),
        "targets": report.get("targets"),
        "feature_availability": report.get("feature_availability"),
        "training_gate": report.get("training_gate"),
        "training_examples_manifest": report.get("training_examples_manifest"),
        "audit_report_manifest": report["audit_report_manifest"],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
