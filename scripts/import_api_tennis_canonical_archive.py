"""Checkpointed, offline projection of saved API-Tennis results into canonical history.

This importer reads PostgreSQL observations only. It never calls the provider,
and commits each bounded page together with its checkpoint so it can resume
without losing or duplicating rows.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "services", "research"))
sys.path.insert(0, os.path.join(ROOT, "sports", "tennis", "src"))

from app.canonical_tennis_projection import project_api_tennis_results  # noqa: E402
from app.archive_backfill import (  # noqa: E402
    AVAILABLE_TIME_CURSOR_VERSION, migrate_to_available_time_cursor,
)
from app.external_tennis_sources import _api_tennis_projection_item  # noqa: E402
from db.connection import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

STATE_KEY = "canonical_projection_backfill"
PARSER_VERSION = "api_tennis_canonical_v1"
BATCH_SIZE = 100


def _read_state(session):
    row = session.execute(text("""
        SELECT state_json FROM external_tennis_source_state
        WHERE source='api_tennis' AND state_key=:key FOR UPDATE
    """), {"key": STATE_KEY}).first()
    state = row[0] if row else {}
    return json.loads(state) if isinstance(state, str) else (state or {})


def _save_state(session, state):
    session.execute(text("""
        INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
        VALUES ('api_tennis',:key,CAST(:state AS jsonb),now())
        ON CONFLICT(source,state_key) DO UPDATE
        SET state_json=excluded.state_json,updated_at=now()
    """), {"key": STATE_KEY, "state": json.dumps(state, ensure_ascii=False)})


def run(*, dry_run: bool = False, max_batches: int | None = None) -> dict:
    totals = {"rows_scanned": 0, "projected": 0, "merged": 0,
              "ambiguous": 0, "unlinked": 0, "unchanged": 0}
    batches = 0
    while max_batches is None or batches < max_batches:
        session = SessionLocal()
        try:
            state = migrate_to_available_time_cursor(_read_state(session))
            last_id = state.get("last_source_match_id")
            last_at = state.get("last_available_at")
            rows = session.execute(text("""
                SELECT source_match_id, available_at, payload
                FROM ext_tennis_match_observations
                WHERE source='api_tennis'
                  AND (:last_at IS NULL OR
                       (available_at,source_match_id) > (CAST(:last_at AS timestamptz),:last_id))
                ORDER BY available_at,source_match_id
                LIMIT :limit
            """), {"last_id": last_id, "last_at": last_at,
                  "limit": BATCH_SIZE}).mappings().all()
            if not rows:
                state.update({"status": "complete", "completed_at": datetime.now(timezone.utc).isoformat(),
                              "parser_version": PARSER_VERSION})
                _save_state(session, state)
                if dry_run:
                    session.rollback()
                    return {"status": "dry_run_complete", **totals}
                session.commit()
                return {"status": "complete", **totals}

            items = []
            for row in rows:
                payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
                event = payload.get("event") or {}
                item = _api_tennis_projection_item(event, row["available_at"])
                if item:
                    items.append(item)
            page_stats = project_api_tennis_results(session, items)
            totals["rows_scanned"] += len(rows)
            for key in page_stats:
                totals[key] += int(page_stats[key])
            state.update({"status": "running", "cursor_version": AVAILABLE_TIME_CURSOR_VERSION,
                          "last_source_match_id": rows[-1]["source_match_id"],
                          "last_available_at": rows[-1]["available_at"].isoformat(),
                          "rows_scanned": int(state.get("rows_scanned", 0)) + len(rows),
                          "projected": int(state.get("projected", 0)) + page_stats["projected"],
                          "merged": int(state.get("merged", 0)) + page_stats["merged"],
                          "ambiguous": int(state.get("ambiguous", 0)) + page_stats["ambiguous"],
                          "unlinked": int(state.get("unlinked", 0)) + page_stats["unlinked"],
                          "unchanged": int(state.get("unchanged", 0)) + page_stats["unchanged"],
                          "parser_version": PARSER_VERSION})
            _save_state(session, state)
            batches += 1
            if dry_run:
                session.rollback()
                return {"status": "dry_run", "batch_rows": len(rows), **totals}
            session.commit()
            print(json.dumps({"status": "running", "batch": batches,
                              "last_source_match_id": rows[-1]["source_match_id"],
                              "batch_projection": page_stats, **totals}), flush=True)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    if max_batches is not None and batches >= max_batches:
        session = SessionLocal()
        try:
            state = migrate_to_available_time_cursor(_read_state(session))
            if state.get("status") != "complete":
                state.pop("paused_at", None)
                state.update({"status": "checkpointed", "checkpointed_at": datetime.now(timezone.utc).isoformat()})
                _save_state(session, state)
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    return {"status": "checkpointed", "batches": batches, **totals}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="project one page then rollback")
    parser.add_argument("--max-batches", type=int, help="commit at most this many pages")
    args = parser.parse_args()
    print(json.dumps(run(dry_run=args.dry_run, max_batches=args.max_batches), ensure_ascii=False), flush=True)
