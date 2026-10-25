"""Idempotently project archived API-Tennis point rows into versioned PBP docs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "services", "research"))
sys.path.insert(0, os.path.join(ROOT, "sports", "tennis", "src"))

from app.external_tennis_sources import _normalize_api_tennis_pbp, _store_result  # noqa: E402
from app.archive_backfill import (  # noqa: E402
    AVAILABLE_TIME_CURSOR_VERSION, migrate_to_available_time_cursor,
)
from db.connection import get_database_url  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

BATCH_SIZE = 100
PARSER_VERSION = "api_tennis_pbp_v1"


def _dt(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def run(*, max_batches: int | None = None) -> dict:
    engine = create_engine(get_database_url(), pool_pre_ping=True)
    total_scanned = total_saved = 0
    batches = 0
    while max_batches is None or batches < max_batches:
        with engine.begin() as conn:
            state = conn.execute(text("""
                SELECT state_json FROM external_tennis_source_state
                WHERE source='api_tennis' AND state_key='pbp_backfill' FOR UPDATE
            """)).scalar()
            if isinstance(state, str):
                state = json.loads(state)
            state = migrate_to_available_time_cursor(state)
            last_at = state.get("last_available_at")
            last_id = state.get("last_source_match_id")
            rows = conn.execute(text("""
                SELECT source_match_id, available_at, payload
                FROM ext_tennis_match_observations
                WHERE source='api_tennis'
                  AND (
                    (CASE WHEN jsonb_typeof(payload #> '{event,pointbypoint}')='array'
                          THEN jsonb_array_length(payload #> '{event,pointbypoint}') > 0 ELSE FALSE END)
                    OR
                    (CASE WHEN jsonb_typeof(payload #> '{event,statistics}')='array'
                          THEN jsonb_array_length(payload #> '{event,statistics}') > 0 ELSE FALSE END)
                  )
                  AND (:last_at IS NULL
                       OR (available_at,source_match_id) > (CAST(:last_at AS timestamptz),:last_id))
                ORDER BY available_at,source_match_id LIMIT :limit
            """), {"last_at": last_at, "last_id": last_id, "limit": BATCH_SIZE}).mappings().all()
            if not rows:
                state.update({"status": "complete", "completed_at": datetime.now(timezone.utc).isoformat(),
                              "parser_version": PARSER_VERSION})
                conn.execute(text("""
                    INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                    VALUES ('api_tennis','pbp_backfill',CAST(:state AS jsonb),now())
                    ON CONFLICT(source,state_key) DO UPDATE SET state_json=excluded.state_json,updated_at=now()
                """), {"state": json.dumps(state)})
                return {"status": "complete", "rows_scanned": total_scanned, "documents_saved": total_saved}

            batch_saved = 0
            for row in rows:
                payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
                event = payload.get("event") or {}
                events, quality = _normalize_api_tennis_pbp(event)
                available = row["available_at"]
                _store_result(conn, event, available)
                if not events:
                    total_scanned += 1
                    continue
                points_json = json.dumps(event.get("pointbypoint"), sort_keys=True, ensure_ascii=False, default=str)
                point_hash = hashlib.sha256(points_json.encode()).hexdigest()
                raw_key = payload.get("raw_object_key")
                result = conn.execute(text("""
                    WITH inserted_document AS (
                      INSERT INTO tennis_point_documents
                        (id,source,source_match_id,source_url,content_hash,archived_path,parser_version,
                         retrieved_at,parsed_at,available_at,availability_status,retrospective,parse_status,quality_json)
                      SELECT gen_random_uuid(),'api_tennis',:mid,
                        'https://api.api-tennis.com/tennis/?method=get_fixtures',:point_hash,:raw_key,
                        :parser,:available,:available,:available,'known',true,'partial',CAST(:quality AS jsonb)
                      WHERE NOT EXISTS (SELECT 1 FROM tennis_point_documents
                        WHERE source='api_tennis' AND source_match_id=:mid AND content_hash=:point_hash
                          AND parser_version=:parser)
                      RETURNING id
                    )
                    INSERT INTO tennis_point_history_versions
                      (id,document_id,source_match_id,fonbet_event_id,parser_version,retrieved_at,parsed_at,
                       available_at,availability_status,events_json,quality_json,retrospective)
                    SELECT gen_random_uuid(),id,:mid,NULL,:parser,:available,:available,:available,
                           'known',CAST(:events AS jsonb),CAST(:quality AS jsonb),true
                    FROM inserted_document RETURNING id
                """), {"mid": row["source_match_id"], "point_hash": point_hash,
                      "raw_key": raw_key, "parser": PARSER_VERSION, "available": available,
                      "quality": json.dumps(quality, ensure_ascii=False),
                      "events": json.dumps(events, ensure_ascii=False)}).first()
                total_scanned += 1
                batch_saved += int(result is not None)
            total_saved += batch_saved
            batches += 1

            state.update({"status": "running", "cursor_version": AVAILABLE_TIME_CURSOR_VERSION,
                          "last_available_at": rows[-1]["available_at"].isoformat(),
                          "last_source_match_id": rows[-1]["source_match_id"],
                          "rows_scanned": int(state.get("rows_scanned", 0)) + len(rows),
                          "documents_saved": int(state.get("documents_saved", 0)) + batch_saved,
                          "parser_version": PARSER_VERSION})
            conn.execute(text("""
                INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                VALUES ('api_tennis','pbp_backfill',CAST(:state AS jsonb),now())
                ON CONFLICT(source,state_key) DO UPDATE SET state_json=excluded.state_json,updated_at=now()
            """), {"state": json.dumps(state)})
        print(json.dumps({"status": "running", "rows_scanned": total_scanned,
                          "documents_saved": total_saved, "last_available_at": rows[-1]["available_at"].isoformat()},
                         ensure_ascii=False), flush=True)
    if max_batches is not None and batches >= max_batches:
        with engine.begin() as conn:
            state = conn.execute(text("""
                SELECT state_json FROM external_tennis_source_state
                WHERE source='api_tennis' AND state_key='pbp_backfill' FOR UPDATE
            """)).scalar()
            if isinstance(state, str):
                state = json.loads(state)
            state = migrate_to_available_time_cursor(state)
            if state.get("status") != "complete":
                state.pop("paused_at", None)
                state.update({"status": "checkpointed", "checkpointed_at": datetime.now(timezone.utc).isoformat()})
                conn.execute(text("""
                    INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                    VALUES ('api_tennis','pbp_backfill',CAST(:state AS jsonb),now())
                    ON CONFLICT(source,state_key) DO UPDATE SET state_json=excluded.state_json,updated_at=now()
                """), {"state": json.dumps(state)})
    return {"status": "checkpointed", "batches": batches,
            "rows_scanned": total_scanned, "documents_saved": total_saved}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-batches", type=int, help="commit at most this many 100-row pages")
    return parser


if __name__ == "__main__":
    args = build_arg_parser().parse_args()
    print(json.dumps(run(max_batches=args.max_batches), ensure_ascii=False), flush=True)
