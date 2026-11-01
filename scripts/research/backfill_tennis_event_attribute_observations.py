"""Idempotently project timestamped legacy tennis event facts into PIT history.

Dry-run is the default. Existing available_at values are preserved verbatim;
reconstructed_at records when this projection was made and never changes PIT.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone

from sqlalchemy import text

from db.connection import SessionLocal


def _timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _candidates(metadata, event_id):
    for attr, valid in (("best_of", {3, 5}), ("surface", {"hard", "clay", "grass", "carpet"})):
        value = metadata.get(attr)
        if attr == "surface" and isinstance(value, str):
            value = value.casefold()
        available = _timestamp(metadata.get(f"{attr}_available_at"))
        source = metadata.get(f"{attr}_source")
        if value not in valid or available is None or not isinstance(source, str) or not source.strip():
            continue
        identity_at = _timestamp(metadata.get(f"{attr}_identity_available_at"))
        if identity_at and identity_at > available:
            # A catalog fact only becomes usable once its identity is confirmed.
            available = identity_at
        evidence = {
            "legacy_projection": True,
            "event_id": str(event_id),
            "available_at_source_field": f"{attr}_available_at",
            "identity_available_at": identity_at.isoformat() if identity_at else None,
            "source_record_id": str(metadata.get(f"{attr}_source_match_id") or event_id),
            "content_hash": metadata.get(f"{attr}_source_content_sha256"),
            "raw_object_key": metadata.get(f"{attr}_source_raw_object_key"),
            "source_value": value,
        }
        body = {"event_id": str(event_id), "attribute": attr, "value": value,
                "source": source, "available_at": available.isoformat(), "evidence": evidence}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        yield {"event_id": event_id, "attribute": attr, "value": value, "source": source,
               "available_at": available, "identity_at": identity_at,
               "record_id": evidence["source_record_id"], "raw_key": evidence["raw_object_key"],
               "content_hash": evidence["content_hash"], "evidence": evidence, "digest": digest}


def run(*, batch_size=250, after_event_id=None, commit=False, cursor_file=None):
    now = datetime.now(timezone.utc)
    examined = candidates = inserted = 0
    rejected = {"metadata_missing": 0, "no_timestamped_explicit_fact": 0}
    cursor = after_event_id
    with SessionLocal() as session:
        while True:
            rows = session.execute(text("""
                SELECT id, metadata_json FROM events
                WHERE sport_code='tennis' AND (:cursor IS NULL OR id::text > :cursor)
                ORDER BY id::text LIMIT :limit
            """), {"cursor": cursor, "limit": batch_size}).all()
            if not rows:
                break
            for event_id, metadata in rows:
                examined += 1
                cursor = str(event_id)
                if not isinstance(metadata, dict):
                    rejected["metadata_missing"] += 1
                    continue
                event_candidates = list(_candidates(metadata, event_id))
                if not event_candidates:
                    rejected["no_timestamped_explicit_fact"] += 1
                for item in event_candidates:
                    candidates += 1
                    if not commit:
                        continue
                    result = session.execute(text("""
                        INSERT INTO tennis_event_attribute_observations
                          (id,event_id,attribute_name,value_json,source,source_record_id,available_at,
                           identity_available_at,raw_object_key,source_content_hash,parser_version,evidence_hash,
                           evidence_json,reconstructed_at,created_at)
                        VALUES (gen_random_uuid(),:event_id,:attribute,CAST(:value AS jsonb),:source,:record_id,
                           :available_at,:identity_at,:raw_key,:content_hash,'legacy_metadata_projection_v1',:digest,
                           CAST(:evidence AS jsonb),:reconstructed_at,:reconstructed_at)
                        ON CONFLICT (event_id,attribute_name,source,available_at,evidence_hash) DO NOTHING
                    """), {"event_id": item["event_id"], "attribute": item["attribute"],
                        "value": json.dumps({"value": item["value"]}), "source": item["source"],
                        "record_id": item["record_id"], "available_at": item["available_at"],
                        "identity_at": item["identity_at"], "raw_key": item["raw_key"],
                        "content_hash": item["content_hash"], "digest": item["digest"],
                        "evidence": json.dumps(item["evidence"]), "reconstructed_at": now})
                    inserted += int(result.rowcount or 0)
            if commit:
                session.commit()
            else:
                session.rollback()
            if cursor_file:
                checkpoint = {"mode": "commit" if commit else "dry_run", "last_event_id": cursor,
                              "examined_events": examined, "candidate_observations": candidates,
                              "inserted_observations": inserted, "updated_at": datetime.now(timezone.utc).isoformat()}
                temporary = str(cursor_file) + ".tmp"
                with open(temporary, "w", encoding="utf-8") as handle:
                    json.dump(checkpoint, handle, sort_keys=True)
                    handle.write("\n")
                os.replace(temporary, cursor_file)
            if len(rows) < batch_size:
                break
    return {"generated_at": now.isoformat(), "mode": "commit" if commit else "dry_run",
            "batch_size": batch_size, "examined_events": examined,
            "candidate_observations": candidates, "inserted_observations": inserted,
            "rejected": rejected, "last_event_id": cursor,
            "resume_after_event_id": cursor if examined else after_event_id,
            "idempotent": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="apply inserts; default is dry-run")
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--after-event-id")
    parser.add_argument("--cursor-file", help="atomically updated batch checkpoint")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 2000:
        parser.error("--batch-size must be between 1 and 2000")
    after_event_id = args.after_event_id
    if not after_event_id and args.cursor_file and os.path.exists(args.cursor_file):
        try:
            with open(args.cursor_file, encoding="utf-8") as handle:
                checkpoint = json.load(handle)
            after_event_id = checkpoint.get("last_event_id") if checkpoint.get("mode") == ("commit" if args.commit else "dry_run") else None
        except (OSError, json.JSONDecodeError):
            parser.error("--cursor-file must contain a valid JSON checkpoint")
    report = run(batch_size=args.batch_size, after_event_id=after_event_id,
                 commit=args.commit, cursor_file=args.cursor_file)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
