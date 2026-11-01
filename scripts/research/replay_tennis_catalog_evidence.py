"""Reparse saved, hash-verified catalogs with original source and identity times.

Default mode rolls back all projections. No provider requests are made.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path

from app.external_tennis_sources import _engine, replay_saved_catalog_evidence
from app.storage import ResearchStorage


class ProjectionTransactions:
    def __init__(self, engine, *, commit: bool):
        self.engine, self.commit = engine, commit

    @contextmanager
    def begin(self):
        with self.engine.connect() as conn:
            transaction = conn.begin()
            try:
                yield conn
            except BaseException:
                transaction.rollback()
                raise
            else:
                if self.commit:
                    transaction.commit()
                else:
                    transaction.rollback()


def replay(*, commit=False, max_catalogs=100):
    engine = _engine()
    storage = ResearchStorage()
    transactions = ProjectionTransactions(engine, commit=commit)
    report = replay_saved_catalog_evidence(engine, storage, projection_engine=transactions, max_catalogs=max_catalogs)
    report["mode"] = "commit" if commit else "dry_run"
    engine.dispose()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--max-catalogs", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.max_catalogs <= 1000:
        parser.error("--max-catalogs must be 1..1000")
    report = replay(commit=args.commit, max_catalogs=args.max_catalogs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"mode": report["mode"], "catalogs": len(report["catalogs"]),
        "verified": sum(item.get("source_hash_verified", False) for item in report["catalogs"]),
        "observations_inserted": sum(item.get("projection", {}).get("observations_inserted", 0)
                                     for item in report["catalogs"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
