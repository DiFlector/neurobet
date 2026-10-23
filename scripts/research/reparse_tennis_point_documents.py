"""Append parser-vNext versions from already archived Flashscore HTML.

Parsing old content does not backdate it: the new parsed version becomes
available at the replay time while the source document's retrieval time and
all prior versions remain unchanged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from db.connection import SessionLocal
from db.models import TennisPointDocument, TennisPointHistoryVersion, TennisPointMatchLink
from tennis_adapter.point_history import PARSER_VERSION, parse_point_history_html
from app.storage import ResearchStorage


def _is_adjacent_duplicate(previous_hash: str | None, current_hash: str) -> bool:
    return previous_hash == current_hash


def reparse(limit: int | None = None, apply: bool = False) -> dict:
    storage = ResearchStorage()
    counts = {"scanned": 0, "eligible": 0, "parsed": 0, "appended": 0,
              "missing_archive": 0, "no_confirmed_link": 0, "already_current": 0,
              "adjacent_unchanged_suppressed": 0, "parse_errors": 0,
              "dry_run": not apply, "parser_version": PARSER_VERSION}
    with SessionLocal() as session:
        query = session.query(TennisPointDocument).filter(
            TennisPointDocument.source == "flashscore",
            TennisPointDocument.parser_version != PARSER_VERSION,
            TennisPointDocument.archived_path.is_not(None),
        ).order_by(TennisPointDocument.retrieved_at, TennisPointDocument.id)
        if limit is not None:
            query = query.limit(limit)
        documents = query.all()
        counts["scanned"] = len(documents)
        parser_rows = session.query(TennisPointDocument.quality_json).filter(
            TennisPointDocument.source == "flashscore",
            TennisPointDocument.parser_version == PARSER_VERSION,
        ).all()
        processed_source_documents = {
            (quality or {}).get("reparsed_from_document_id")
            for (quality,) in parser_rows
            if (quality or {}).get("reparsed_from_document_id")
        }
        previous_hash_by_stream = {}
        for old in documents:
            stream = (old.source, old.source_match_id, old.source_url)
            previous_hash = previous_hash_by_stream.get(stream)
            previous_hash_by_stream[stream] = old.content_hash
            if str(old.id) in processed_source_documents:
                counts["already_current"] += 1
                continue
            # Suppress only an unchanged adjacent observation. A later A after
            # A → B → A is a real correction transition and must be appended
            # even when that HTML hash appeared earlier in this stream.
            if _is_adjacent_duplicate(previous_hash, old.content_hash):
                counts["adjacent_unchanged_suppressed"] += 1
                continue
            link = session.query(TennisPointMatchLink).filter(
                TennisPointMatchLink.source_match_id == old.source_match_id,
                TennisPointMatchLink.fonbet_event_id.is_not(None),
                TennisPointMatchLink.available_at <= datetime.now(timezone.utc),
            ).order_by(TennisPointMatchLink.available_at.desc()).first()
            if not link or link.status not in ("matched", "manual"):
                counts["no_confirmed_link"] += 1
                continue
            counts["eligible"] += 1
            archived = storage.read_document(old.archived_path)
            html = archived.get("html") if isinstance(archived, dict) else None
            raw_feed = archived.get("raw_feed") if isinstance(archived, dict) else None
            if not (isinstance(html, str) and html or isinstance(raw_feed, str) and raw_feed):
                counts["missing_archive"] += 1
                continue
            try:
                if isinstance(raw_feed, str) and raw_feed:
                    from app.point_history import _parse_point_history_feed
                    parsed = _parse_point_history_feed(raw_feed)
                else:
                    parsed = parse_point_history_html(html)
            except Exception:
                counts["parse_errors"] += 1
                continue
            quality = {**(parsed.get("quality") or {}),
                       "set_number": (old.quality_json or {}).get("set_number"),
                       "fetch_transport": (old.quality_json or {}).get("fetch_transport", "browser"),
                       "reparsed_from_document_id": str(old.id)}
            if not parsed.get("events"):
                quality.setdefault("gaps", []).append("HISTORY_EMPTY_OR_UNRECOGNIZED")
            counts["parsed"] += 1
            if not apply:
                continue
            now = datetime.now(timezone.utc)
            # Reuse the immutable raw document in object storage. Store a new
            # DB document/version pair with new parser and availability times.
            document = TennisPointDocument(
                source=old.source, source_match_id=old.source_match_id,
                source_url=old.source_url, content_hash=old.content_hash,
                archived_path=old.archived_path, parser_version=PARSER_VERSION,
                retrieved_at=old.retrieved_at, parsed_at=now, available_at=now,
                availability_status="known", retrospective=True,
                parse_status="complete" if quality.get("complete") else "partial",
                quality_json=quality,
            )
            session.add(document)
            session.flush()
            events = [{**item, "retrospective": True} for item in parsed.get("events", [])]
            session.add(TennisPointHistoryVersion(
                document_id=document.id, source_match_id=old.source_match_id,
                fonbet_event_id=link.fonbet_event_id, parser_version=PARSER_VERSION,
                retrieved_at=old.retrieved_at, parsed_at=now, available_at=now,
                availability_status="known", events_json=events,
                quality_json=quality, retrospective=True,
            ))
            session.commit()
            counts["appended"] += 1
            processed_source_documents.add(str(old.id))
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--apply", action="store_true", help="append new parser versions")
    args = parser.parse_args()
    print(reparse(args.limit, args.apply))
