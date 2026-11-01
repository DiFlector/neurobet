"""Wait for the Flashscore cooldown, then monitor the junior PBP source queue.

Read-only with respect to production: it does not dispatch, retry, or modify
jobs. It records a JSON snapshot after cooldown and as the queue progresses.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import time

from sqlalchemy import text
from db.connection import SessionLocal


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def snapshot(source_ids: list[str]) -> dict:
    now = datetime.now(timezone.utc)
    result = {"checked_at": now.isoformat(), "matches": []}
    with SessionLocal() as session:
        rows = session.execute(text("""
            select ids.source_match_id,
                   j.status as job_status, j.attempts, j.last_error, j.next_run_at,
                   count(distinct d.id) as documents,
                   count(distinct v.id) as versions,
                   bool_or((d.quality_json->>'complete')::boolean) as any_complete,
                   max((d.quality_json->>'confirmed_points')::int) as max_confirmed_points,
                   count(distinct l.id) filter (where l.fonbet_event_id is not null) as fonbet_links
              from unnest(CAST(:ids AS text[])) as ids(source_match_id)
              left join tennis_point_source_refresh_jobs j using (source_match_id)
              left join tennis_point_documents d on d.source_match_id=ids.source_match_id and d.source='flashscore'
              left join tennis_point_history_versions v on v.source_match_id=ids.source_match_id and v.document_id=d.id
              left join tennis_point_match_links l on l.source_match_id=ids.source_match_id
             group by ids.source_match_id,j.status,j.attempts,j.last_error,j.next_run_at
             order by ids.source_match_id
        """), {"ids": source_ids}).mappings().all()
        for row in rows:
            result["matches"].append({
                "source_match_id": row["source_match_id"],
                "job_status": row["job_status"], "attempts": row["attempts"],
                "last_error": row["last_error"],
                "next_run_at": row["next_run_at"].isoformat() if row["next_run_at"] else None,
                "documents": row["documents"], "versions": row["versions"],
                "any_complete": bool(row["any_complete"]),
                "max_confirmed_points": row["max_confirmed_points"] or 0,
                "fonbet_links": row["fonbet_links"],
            })
    result["summary"] = {
        "source_matches": len(result["matches"]),
        "jobs_completed": sum(row["job_status"] == "completed" for row in result["matches"]),
        "jobs_failed_or_blocked": sum(row["job_status"] in {"failed", "blocked"} for row in result["matches"]),
        "with_point_documents": sum(row["documents"] > 0 for row in result["matches"]),
        "with_complete_sequences": sum(row["any_complete"] and row["max_confirmed_points"] > 0 for row in result["matches"]),
        "fonbet_links": sum(row["fonbet_links"] for row in result["matches"]),
    }
    result["all_complete"] = (
        result["summary"]["source_matches"] == len(source_ids)
        and result["summary"]["jobs_completed"] == len(source_ids)
        and result["summary"]["with_complete_sequences"] == len(source_ids)
        and result["summary"]["fonbet_links"] == 0
    )
    result["all_terminal"] = all(row["job_status"] in {"completed", "failed", "blocked"}
                                 for row in result["matches"])
    return result


def monitor(queue_path: Path, output_path: Path, start_at: datetime,
            poll_seconds: int, max_wait_hours: float) -> dict:
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    source_ids = list(queue.get("enqueued_source_matches") or [])
    if not source_ids:
        raise ValueError("queue report contains no enqueued source IDs")
    while datetime.now(timezone.utc) < start_at:
        time.sleep(min(30, max(1, int((start_at - datetime.now(timezone.utc)).total_seconds()))))
    deadline = datetime.now(timezone.utc) + timedelta(hours=max_wait_hours)
    history = []
    while True:
        current = snapshot(source_ids)
        history.append(current)
        final = {"started_at": start_at.isoformat(), "queue_report": str(queue_path),
                 "poll_seconds": poll_seconds, "timeout_at": deadline.isoformat(),
                 "checks": history, "latest": current,
                 "result": "complete" if current["all_complete"] else
                          "terminal_with_gaps" if current["all_terminal"] else
                          "timed_out" if datetime.now(timezone.utc) >= deadline else "monitoring"}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(output_path)
        print(json.dumps({"checked_at": current["checked_at"], "summary": current["summary"],
                          "result": final["result"]}, ensure_ascii=False), flush=True)
        if current["all_complete"] or current["all_terminal"] or datetime.now(timezone.utc) >= deadline:
            return final
        time.sleep(max(10, poll_seconds))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-at", type=_utc, required=True)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--max-wait-hours", type=float, default=6)
    args = parser.parse_args()
    print(json.dumps(monitor(args.queue, args.output, args.start_at,
                             args.poll_seconds, args.max_wait_hours),
                     ensure_ascii=False), flush=True)
