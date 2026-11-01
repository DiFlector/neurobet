#!/usr/bin/env python3
"""PIT-safe backfill of tennis best-of from timestamped normal Fonbet results.

Dry-run by default. `--commit` writes only missing `best_of` metadata, timestamped
at the official result's stored availability time. No event snapshots are changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import NullPool

SET_RESULT = re.compile(r"^\s*(\d+)\s*[:\-]\s*(\d+)(?:\s*\(|\s*$)")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(timezone.utc)


def _classify(metadata: Any, scheduled_start_at: datetime, now: datetime) -> tuple[int | None, str]:
    if not isinstance(metadata, dict):
        return None, "invalid_metadata"
    if "best_of" in metadata:
        return None, "already_has_format"
    result = metadata.get("fonbet_official_result")
    if not isinstance(result, dict):
        return None, "missing_official_result"
    if result.get("status") != "FINISHED" or result.get("winner") not in {"player_a", "player_b"}:
        return None, "not_normal_finished"
    if result.get("is_retired") is not False or result.get("is_walkover") is not False:
        return None, "retirement_or_walkover_or_unknown"
    available_at = _parse_time(result.get("available_at"))
    if available_at is None:
        return None, "missing_or_invalid_result_available_at"
    start = scheduled_start_at
    if start.tzinfo is None:
        return None, "missing_schedule_timezone"
    if available_at < start.astimezone(timezone.utc):
        return None, "result_available_before_scheduled_start"
    if available_at > now:
        return None, "result_available_in_future"
    final_score = str(result.get("final_score") or "")
    match = SET_RESULT.match(final_score)
    if match is None:
        return None, "unrecognized_final_set_score"
    sets_a, sets_b = int(match.group(1)), int(match.group(2))
    if max(sets_a, sets_b) == 2 and min(sets_a, sets_b) in (0, 1):
        return 3, "ok"
    if max(sets_a, sets_b) == 3 and min(sets_a, sets_b) in (0, 1, 2):
        return 5, "ok"
    return None, "final_score_not_normal_match_ending"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="write verified metadata to PostgreSQL")
    parser.add_argument("--output", default="docs/research/tennis_best_of_backfill_2026-10-26.json")
    args = parser.parse_args()

    from db.connection import get_database_url

    engine = create_engine(get_database_url(), poolclass=NullPool, pool_pre_ping=True)
    now = datetime.now(timezone.utc)
    skipped: Counter[str] = Counter()
    candidates: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id::text AS id, source_event_id, scheduled_start_at, metadata_json
            FROM events
            WHERE sport_code='tennis' AND source='fonbet'
            ORDER BY source_event_id
        """)).mappings()
        for row in rows:
            best_of, reason = _classify(row["metadata_json"], row["scheduled_start_at"], now)
            if best_of is None:
                skipped[reason] += 1
                continue
            result = row["metadata_json"]["fonbet_official_result"]
            available_at = _parse_time(result["available_at"])
            candidates.append({
                "id": row["id"], "source_event_id": str(row["source_event_id"]),
                "best_of": best_of, "final_score": str(result["final_score"]),
                "available_at": available_at.isoformat(),
                "result_source_event_id": str(result.get("source_event_id") or ""),
            })
            digest.update(f"{row['id']}|{best_of}|{available_at.isoformat()}\n".encode())

    committed = 0
    if args.commit and candidates:
        with engine.begin() as conn:
            for candidate in candidates:
                result = conn.execute(text("""
                    SELECT metadata_json FROM events WHERE id=CAST(:id AS uuid) FOR UPDATE
                """), {"id": candidate["id"]}).scalar_one_or_none()
                if not isinstance(result, dict):
                    continue
                current = dict(result)
                current_result = current.get("fonbet_official_result")
                if not isinstance(current_result, dict):
                    continue
                # Recheck under lock; the event could have been corrected since dry-run.
                best_of, _reason = _classify(current, datetime.min.replace(tzinfo=timezone.utc), now)
                if best_of != candidate["best_of"]:
                    continue
                available_at = _parse_time(current_result.get("available_at"))
                if available_at is None:
                    continue
                current.update({
                    "best_of": best_of,
                    "best_of_available_at": available_at.isoformat(),
                    "best_of_source": "fonbet_official_result",
                    "best_of_evidence": {
                        "rule": "normal_official_final_set_count",
                        "final_score": str(current_result["final_score"]),
                        "result_status": "FINISHED",
                        "is_retired": False,
                        "is_walkover": False,
                        "result_source_event_id": current_result.get("source_event_id"),
                        "result_available_at": available_at.isoformat(),
                    },
                })
                updated = conn.execute(text("""
                    UPDATE events SET metadata_json=CAST(:metadata AS jsonb),updated_at=now()
                    WHERE id=CAST(:id AS uuid)
                      AND NOT (metadata_json ? 'best_of')
                    RETURNING id
                """), {"id": candidate["id"], "metadata": json.dumps(current)}).first()
                committed += int(updated is not None)

    report = {
        "generated_at": now.isoformat(),
        "source": "fonbet_official_result",
        "rule": "normal FINISHED result, no retirement/walkover, result available_at >= scheduled start; 2-set win => BO3, 3-set win => BO5",
        "mode": "commit" if args.commit else "dry_run",
        "events_scanned": sum(skipped.values()) + len(candidates),
        "candidates": len(candidates),
        "candidates_by_best_of": dict(Counter(str(row["best_of"]) for row in candidates)),
        "skipped": dict(skipped),
        "candidate_digest_sha256": digest.hexdigest(),
        "rows_committed": committed,
        "pit_note": "best_of_available_at is the result source availability time; no pre-result snapshot is made eligible by this backfill",
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
