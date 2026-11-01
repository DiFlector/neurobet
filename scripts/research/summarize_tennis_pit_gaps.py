"""Summarize the six mandatory source groups from a strict-builder JSONL."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def summarize(path: Path) -> dict:
    group_pass, histogram = Counter(), Counter()
    targets = defaultdict(lambda: {"examples": 0, "events": set(), "accepted_examples": 0, "accepted_events": set()})
    candidates, all_groups = [], set()
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        row = json.loads(line)
        groups = defaultdict(list)
        for feature, group in row["feature_source_groups"].items():
            groups[group].append(feature)
        if len(groups) != 6:
            raise ValueError("expected_six_source_groups")
        all_groups.update(groups)
        failed = set()
        for reason in row["rejection_reasons"]:
            for group, features in groups.items():
                if reason.endswith(":" + group) or any(reason == "feature_value_missing:" + feature for feature in features):
                    failed.add(group)
        passed = set(groups) - failed
        group_pass.update(passed)
        histogram[len(passed)] += 1
        event_id = row["provenance"]["fonbet_event_id"]
        target = targets[row["target"]]
        target["examples"] += 1
        target["events"].add(event_id)
        if row["status"] == "accepted":
            target["accepted_examples"] += 1
            target["accepted_events"].add(event_id)
        candidates.append({"event_id": event_id, "snapshot_id": row["provenance"]["snapshot_id"],
            "target": row["target"], "cutoff": row["snapshot_observed_at"],
            "verified_groups": sorted(passed), "missing_groups": sorted(failed),
            "rejection_reasons": row["rejection_reasons"]})
    for target in targets.values():
        target["events"] = len(target["events"])
        target["accepted_events"] = len(target["accepted_events"])
    seen, ranked = set(), []
    for item in sorted(candidates, key=lambda row: (len(row["missing_groups"]), row["event_id"], row["cutoff"])):
        if item["event_id"] in seen:
            continue
        seen.add(item["event_id"])
        ranked.append(item)
        if len(ranked) == 20:
            break
    return {"generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "verified_groups_per_example": dict(sorted(histogram.items())),
            "group_verified_examples": {group: group_pass[group] for group in sorted(all_groups)},
            "targets": dict(targets), "nearest_restoration_candidates": ranked}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = summarize(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "nearest_restoration_candidates"}))


if __name__ == "__main__":
    main()
