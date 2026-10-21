"""Read-only review of Fonbet snapshots that already show a completed match score.

This does not rewrite source availability or open the training gate. It measures
whether an immutable Fonbet state snapshot can prove an earlier result-observed
time than a later backfill of official result metadata.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from db.connection import SessionLocal
from db.models import Event, EventStateSnapshot
from tennis_label_audit import _discipline, _official_winner
from tennis_result_observation import score_signature


def build_report() -> dict:
    now = datetime.now(timezone.utc)
    sample_limit = max(0, int(os.getenv("TENNIS_RESULT_OBSERVATION_LIMIT_EVENTS", "0")))
    report = {
        "generated_at": now.isoformat(),
        "source": "Fonbet EventStateSnapshot.score exact normalized match to official final score",
        "mutates_database": False,
        "sample_limit": sample_limit or None,
        "eligible_numeric_singles": 0,
        "valid_final_results": 0,
        "snapshot_rows_scanned": 0,
        "events_with_exact_final_score_snapshot": 0,
        "events_snapshot_precedes_metadata_availability": 0,
        "events_without_result_time_but_with_snapshot_evidence": 0,
        "snapshot_statuses": {},
        "observed_at_range": {"min": None, "max": None},
        "examples": [],
    }
    with SessionLocal() as session:
        requests: list[object] = []
        event_by_id = {}
        signature_by_event = {}
        matched_events = set()
        events = session.query(Event).filter(
            Event.source == "fonbet", Event.sport_code == "tennis",
        ).yield_per(500)
        for event in events:
            if not str(event.source_event_id).isdigit() or _discipline(event, "") != "singles":
                continue
            report["eligible_numeric_singles"] += 1
            winner, _reason = _official_winner(event)
            if winner is None:
                continue
            report["valid_final_results"] += 1
            metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
            official = metadata.get("fonbet_official_result") or {}
            final_score = str(official.get("final_score") or "")
            signature = score_signature(final_score)
            if signature:
                requests.append(event.id)
                event_by_id[event.id] = event
                signature_by_event[event.id] = signature
                if sample_limit and len(requests) >= sample_limit:
                    break

        for offset in range(0, len(requests), 200):
            batch = set(requests[offset:offset + 200])
            rows = session.query(
                EventStateSnapshot.event_id, EventStateSnapshot.observed_at,
                EventStateSnapshot.status, EventStateSnapshot.score,
            ).filter(
                EventStateSnapshot.event_id.in_(batch),
            ).order_by(
                EventStateSnapshot.event_id, EventStateSnapshot.observed_at, EventStateSnapshot.id,
            ).yield_per(5000)
            for row in rows:
                report["snapshot_rows_scanned"] += 1
                if row.event_id in matched_events or score_signature(row.score) != signature_by_event.get(row.event_id):
                    continue
                matched_events.add(row.event_id)
                report["events_with_exact_final_score_snapshot"] += 1
                report["snapshot_statuses"][row.status] = report["snapshot_statuses"].get(row.status, 0) + 1
                iso = row.observed_at.isoformat()
                lower, upper = report["observed_at_range"]["min"], report["observed_at_range"]["max"]
                report["observed_at_range"]["min"] = min(lower, iso) if lower else iso
                report["observed_at_range"]["max"] = max(upper, iso) if upper else iso
                event = event_by_id.get(row.event_id)
                metadata = event.metadata_json if event and isinstance(event.metadata_json, dict) else {}
                official = metadata.get("fonbet_official_result") or {}
                raw_available = official.get("available_at") or official.get("observed_at")
                known_at = None
                if isinstance(raw_available, str):
                    try:
                        known_at = datetime.fromisoformat(raw_available.replace("Z", "+00:00"))
                        known_at = known_at.replace(tzinfo=timezone.utc) if known_at.tzinfo is None else known_at.astimezone(timezone.utc)
                    except ValueError:
                        pass
                if known_at is None:
                    report["events_without_result_time_but_with_snapshot_evidence"] += 1
                elif row.observed_at.astimezone(timezone.utc) < known_at:
                    report["events_snapshot_precedes_metadata_availability"] += 1
                if len(report["examples"]) < 25:
                    report["examples"].append({
                        "fonbet_event_id": str(row.event_id),
                        "fonbet_source_event_id": str(event.source_event_id) if event else None,
                        "official_final_score": str((event.metadata_json or {}).get("fonbet_official_result", {}).get("final_score", "")),
                        "observed_score": row.score,
                        "snapshot_status": row.status, "snapshot_observed_at": iso,
                        "metadata_available_at": known_at.isoformat() if known_at else None,
                    })
    return report


if __name__ == "__main__":
    report = build_report()
    path = os.getenv("TENNIS_RESULT_OBSERVATION_REPORT", "docs/research/tennis_result_observation_review_2026-10-21.json")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(path + ".tmp", path)
    print(json.dumps({key: value for key, value in report.items() if key != "examples"}, ensure_ascii=False))
    print(f"report={path}")
