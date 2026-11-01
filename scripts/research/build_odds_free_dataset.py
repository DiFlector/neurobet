"""Build a PIT-checked odds-free tennis candidate dataset without training."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from itertools import islice
from collections import Counter
import os
from pathlib import Path

from app.odds_free_dataset import build_odds_free_dataset


def _iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"line_{number}_must_be_an_object")
            yield value


def _read_jsonl(path: Path) -> list[dict]:
    return list(_iter_jsonl(path))


def _stream_build(examples, handle, *, dataset_as_of, chunk_size=1000):
    """Use the same validator and summary with bounded memory."""
    iterator = iter(examples)
    summary = None
    counter_keys = ("rejection_reason_counts", "unknown_optional_feature_reasons")
    bool_keys = ("pit_certified", "feature_pit_certified")
    while True:
        chunk = list(islice(iterator, chunk_size))
        if not chunk:
            break
        rows, part = build_odds_free_dataset(chunk, dataset_as_of=dataset_as_of)
        if summary is None:
            summary = part
        else:
            if summary["feature_contract"] != part["feature_contract"]:
                raise ValueError("mixed_odds_free_feature_contracts_in_dataset")
            for key, value in part.items():
                if key in counter_keys:
                    counts = Counter(summary[key]); counts.update(value)
                    summary[key] = dict(sorted(counts.items()))
                elif key in bool_keys:
                    summary[key] = summary[key] and value
                elif isinstance(value, int) and not isinstance(value, bool):
                    summary[key] += value
                elif summary[key] != value:
                    raise ValueError(f"builder_summary_mismatch:{key}")
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return summary if summary is not None else build_odds_free_dataset([], dataset_as_of=dataset_as_of)[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="JSONL provenance examples from tennis_label_audit")
    parser.add_argument("--output", required=True, help="Output JSONL path for accepted and rejected examples")
    parser.add_argument("--dataset-as-of", help="UTC ISO timestamp; defaults to current UTC")
    parser.add_argument("--stream", action="store_true", help="Validate the entire input with bounded memory")
    args = parser.parse_args()
    dataset_as_of = (datetime.fromisoformat(args.dataset_as_of.replace("Z", "+00:00"))
                     if args.dataset_as_of else datetime.now(timezone.utc))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        if args.stream:
            summary = _stream_build(_iter_jsonl(Path(args.input)), handle, dataset_as_of=dataset_as_of)
        else:
            rows, summary = build_odds_free_dataset(_read_jsonl(Path(args.input)), dataset_as_of=dataset_as_of)
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, output)
    summary["input_path"] = str(Path(args.input))
    summary["output_path"] = str(output)
    summary_path = output.with_suffix(output.suffix + ".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
