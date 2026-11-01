"""Materialize denominator-backed API-Tennis serve observations from stored raw rows.

This is an offline, bounded replay. It makes no provider requests and uses each
observation's original `available_at`; only confirmed API-Tennis identity links
to a single Fonbet profile can produce a serve-log row.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import text

from app.external_tennis_sources import (
    _api_tennis_serve_point_counts,
    _engine,
    _persist_api_tennis_serve_point_counts,
)


def _played_at(event: dict) -> datetime | None:
    event_date = str(event.get("event_date") or "")
    event_time = str(event.get("event_time") or "00:00")
    try:
        return datetime.fromisoformat(f"{event_date}T{event_time}").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=1000,
                        help="maximum recent stored source observations to replay (1..5000)")
    parser.add_argument("--commit", action="store_true",
                        help="persist idempotent serve-log rows; without this flag, perform a read-only count")
    args = parser.parse_args()
    limit = max(1, min(5000, args.limit))
    engine = _engine()
    scanned = 0
    with engine.begin() as conn:
        rows = conn.execute(text("""
          WITH recent AS (
            SELECT source_match_id,available_at,payload->'event' AS event
            FROM ext_tennis_match_observations
            WHERE source='api_tennis' AND payload ? 'event'
              AND jsonb_typeof(payload->'event')='object'
            ORDER BY available_at DESC,source_match_id
            LIMIT :limit
          )
          SELECT source_match_id,available_at,event
          FROM recent
          ORDER BY available_at,source_match_id
        """), {"limit": limit}).mappings().all()
        eligible = 0
        inserted = 0
        for row in rows:
            scanned += 1
            event = row["event"]
            if str(event.get("event_status") or "").casefold() not in {"finished", "ended", "completed"}:
                continue
            if not _api_tennis_serve_point_counts(event):
                continue
            played_at = _played_at(event)
            if played_at is None:
                continue
            eligible += 1
            if args.commit:
                # available_at is copied unchanged from the saved source observation.
                event = {**event, "event_key": row["source_match_id"]}
                inserted += _persist_api_tennis_serve_point_counts(
                    conn, event, row["available_at"], played_at
                )
        if not args.commit:
            conn.rollback()
            print(f"read_only scanned={scanned} eligible_with_counts={eligible} inserted=0")
            engine.dispose()
            return 0
        print(f"committed scanned={scanned} eligible_with_counts={eligible} inserted={inserted}")
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
