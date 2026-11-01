"""Join the full audit with the strict odds-free builder's PIT certification."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def finalize(audit_path: Path, builder_summary_path: Path, output_path: Path) -> dict:
    audit_bytes = audit_path.read_bytes()
    summary_bytes = builder_summary_path.read_bytes()
    report = json.loads(audit_bytes)
    summary = json.loads(summary_bytes)
    feature = report.setdefault("feature_availability", {})
    checked = int(summary.get("input_examples", 0))
    accepted = int(summary.get("accepted_examples", 0))
    if checked != int(feature.get("checked_examples", -1)):
        raise ValueError("builder_input_count_does_not_match_audit_examples")
    feature["strict_builder"] = summary
    feature["certified_examples"] = accepted
    feature["uncertified_examples"] = checked - accepted
    feature["fully_source_observed_examples"] = accepted
    training = report.setdefault("training_gate", {})
    pit_ok = checked > 0 and accepted == checked and summary.get("pit_certified") is True
    training["point_in_time_certified"] = pit_ok
    training["strict_builder_accepted_examples"] = accepted
    training["strict_builder_input_examples"] = checked
    reasons = list(training.get("reasons") or [])
    if not pit_ok and "odds_free_feature_availability_not_certified" not in reasons:
        reasons.append("odds_free_feature_availability_not_certified")
    training["reasons"] = sorted(set(reasons))
    if not pit_ok or reasons:
        training["status"] = "blocked"
    report["pit_finalized_at"] = datetime.now(timezone.utc).isoformat()
    rendered = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_bytes(rendered)
    temporary.replace(output_path)
    return {
        "output": str(output_path), "sha256": hashlib.sha256(rendered).hexdigest(),
        "audit_input_sha256": hashlib.sha256(audit_bytes).hexdigest(),
        "builder_summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
        "checked_examples": checked, "accepted_examples": accepted,
        "pit_certified": pit_ok, "training_gate": training.get("status"),
        "reasons": training["reasons"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--builder-summary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(finalize(args.audit, args.builder_summary, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
