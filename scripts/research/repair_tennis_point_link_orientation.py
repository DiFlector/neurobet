"""Recover missing Flashscore side orientation from archived DOM and confirmed IDs.

Dry-run by default. Applying appends a new link version at the repair time; it
never edits or backdates the previous decision.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone

from db.connection import SessionLocal
from db.models import (Event, TennisPlayerIdentityLink, TennisPointDocument,
                       TennisPointMatchLink)
from app.point_history import _source_dom_side_orientation
from app.storage import ResearchStorage


def repair(*, limit: int = 100, apply: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    counts = {"scanned_links": 0, "candidate_links": 0, "confirmed_ids": 0,
              "archived_documents": 0, "orientation_resolved": 0, "appended": 0,
              "missing_event": 0, "missing_confirmed_identity": 0,
              "missing_archive": 0, "dom_identity_mismatch": 0,
              "dry_run": not apply}
    storage = ResearchStorage()
    with SessionLocal() as session:
        all_links = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.fonbet_event_id.is_not(None),
            TennisPointMatchLink.available_at <= now,
        ).order_by(TennisPointMatchLink.available_at, TennisPointMatchLink.id).all()
        latest = {}
        for link in all_links:
            latest[(link.fonbet_event_id, link.source_match_id)] = link
        candidates = [row for row in latest.values()
                      if row.status in ("matched", "manual")
                      and not isinstance((row.evidence_json or {}).get("external_home_is_fonbet_a"), bool)]
        candidates.sort(key=lambda row: (row.available_at, str(row.id)))
        candidates = candidates[:max(0, limit)]
        counts["scanned_links"] = len(latest)
        counts["candidate_links"] = len(candidates)

        event_ids = {row.fonbet_event_id for row in candidates}
        events = {row.id: row for row in session.query(Event).filter(Event.id.in_(event_ids)).all()} if event_ids else {}
        participant_ids = {pid for event in events.values()
                           for pid in (event.participant_a_id, event.participant_b_id) if pid}
        identity_versions = defaultdict(list)
        if participant_ids:
            rows = session.query(TennisPlayerIdentityLink).filter(
                TennisPlayerIdentityLink.participant_id.in_(participant_ids),
                TennisPlayerIdentityLink.source == "flashscore",
                TennisPlayerIdentityLink.available_at <= now,
            ).order_by(TennisPlayerIdentityLink.available_at, TennisPlayerIdentityLink.id).all()
            for row in rows:
                identity_versions[row.participant_id].append(row)

        source_ids = {row.source_match_id for row in candidates}
        docs_by_source = defaultdict(list)
        if source_ids:
            docs = session.query(TennisPointDocument).filter(
                TennisPointDocument.source == "flashscore",
                TennisPointDocument.source_match_id.in_(source_ids),
                TennisPointDocument.archived_path.is_not(None),
                TennisPointDocument.retrieved_at <= now,
            ).order_by(TennisPointDocument.retrieved_at, TennisPointDocument.id).all()
            for doc in docs:
                docs_by_source[doc.source_match_id].append(doc)

        decisions = []
        for link in candidates:
            event = events.get(link.fonbet_event_id)
            if event is None:
                counts["missing_event"] += 1
                decisions.append({"link_id": str(link.id), "reason": "missing_event"})
                continue
            confirmed_ids = []
            for participant_id in (event.participant_a_id, event.participant_b_id):
                versions = identity_versions.get(participant_id, [])
                latest_identity = versions[-1] if versions else None
                if latest_identity and latest_identity.status == "confirmed":
                    confirmed_ids.append(latest_identity.source_player_id)
                else:
                    confirmed_ids.append(None)
            if not all(confirmed_ids) or confirmed_ids[0] == confirmed_ids[1]:
                counts["missing_confirmed_identity"] += 1
                decisions.append({"link_id": str(link.id), "reason": "missing_confirmed_identity"})
                continue
            counts["confirmed_ids"] += 1
            documents = docs_by_source.get(link.source_match_id, [])
            orientation = None
            witness = None
            for document in reversed(documents):
                archived = storage.read_document(document.archived_path)
                html = archived.get("html") if isinstance(archived, dict) else None
                if not isinstance(html, str) or not html:
                    continue
                counts["archived_documents"] += 1
                orientation = _source_dom_side_orientation(html, *confirmed_ids)
                if orientation is not None:
                    witness = document
                    break
            if orientation is None:
                if not documents:
                    counts["missing_archive"] += 1
                    reason = "missing_archive"
                else:
                    counts["dom_identity_mismatch"] += 1
                    reason = "dom_identity_mismatch"
                decisions.append({"link_id": str(link.id), "reason": reason})
                continue

            counts["orientation_resolved"] += 1
            decision = {"link_id": str(link.id), "fonbet_event_id": str(link.fonbet_event_id),
                        "source_match_id": link.source_match_id,
                        "external_home_is_fonbet_a": orientation,
                        "raw_hash": witness.content_hash, "document_id": str(witness.id)}
            decisions.append(decision)
            if apply:
                evidence = dict(link.evidence_json or {})
                evidence.update({
                    "external_home_is_fonbet_a": orientation,
                    "side_orientation_source": "archived_scoreboard_dom_confirmed_profile_ids",
                    "orientation_repair": {
                        "resolution": "confirmed", "based_on_link_id": str(link.id),
                        "identity_link_ids": [str(identity_versions[pid][-1].id)
                            for pid in (event.participant_a_id, event.participant_b_id)],
                        "archived_document_id": str(witness.id),
                        "raw_hash": witness.content_hash,
                        "raw_available_at": witness.available_at.isoformat() if witness.available_at else None,
                        "resolved_at": now.isoformat(),
                    },
                })
                session.add(TennisPointMatchLink(
                    source_match_id=link.source_match_id, fonbet_event_id=link.fonbet_event_id,
                    status=link.status, confidence=link.confidence,
                    evidence_json=evidence, available_at=now,
                ))
                counts["appended"] += 1
        if apply:
            session.commit()
        result = {**counts, "decisions": decisions}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--apply", action="store_true", help="append verified link versions")
    args = parser.parse_args()
    import json
    print(json.dumps(repair(limit=args.limit, apply=args.apply), ensure_ascii=False, sort_keys=True))
