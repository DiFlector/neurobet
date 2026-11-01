"""Discover real archived Flashscore junior match URLs and enqueue source-only PBP.

Default mode is read-only. --apply adds durable jobs through the existing
source-keyed PBP enqueue helper and never creates a Fonbet match link.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from db.connection import SessionLocal
from db.models.external_tennis import (
    ExtTennisMatch, ExtTennisMatchObservation, FlashscoreImportJob, TennisPointDocument,
    TennisPointHistoryVersion, TennisPointSourceRefreshJob,
)
from app.storage import ResearchStorage
from app.point_history import _source_match_summary_url


def collect(apply: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    storage = ResearchStorage()
    report = {"generated_at": now.isoformat(), "dry_run": not apply,
              "scope": "Flashscore junior source-only retrospective PBP; no Fonbet links",
              "matches": [], "errors": []}
    with SessionLocal() as session:
        matches = session.query(
            ExtTennisMatch.source_match_id, ExtTennisMatch.tournament,
            ExtTennisMatch.played_at, ExtTennisMatch.winner_name,
            ExtTennisMatch.loser_name, ExtTennisMatch.gender,
        ).filter(
            ExtTennisMatch.source == "flashscore",
            ExtTennisMatch.tour_level == "junior",
        ).order_by(ExtTennisMatch.played_at, ExtTennisMatch.source_match_id).all()
        source_ids = {row.source_match_id for row in matches}
        urls_by_id = defaultdict(set)
        profile_urls = set()
        observation_rows = session.execute(
            __import__("sqlalchemy").text("""
                select distinct source_match_id, source_url
                from ext_tennis_match_observations
                where source='flashscore' and source_match_id = any(:ids)
            """), {"ids": list(source_ids)}).all() if source_ids else []
        for source_id, profile_url in observation_rows:
            urls_by_id[source_id].add(profile_url)
            profile_urls.add(profile_url)
        latest_observations = {}
        if source_ids:
            observations = session.query(ExtTennisMatchObservation).filter(
                ExtTennisMatchObservation.source == "flashscore",
                ExtTennisMatchObservation.source_match_id.in_(source_ids),
            ).order_by(
                ExtTennisMatchObservation.source_match_id,
                ExtTennisMatchObservation.available_at.desc(),
            ).distinct(ExtTennisMatchObservation.source_match_id).all()
            latest_observations = {row.source_match_id: row for row in observations}
        jobs = session.query(FlashscoreImportJob.result_json).filter(
            FlashscoreImportJob.status == "completed",
        ).order_by(FlashscoreImportJob.updated_at.desc()).all()
        profile_archives = {}
        for (result_json,) in jobs:
            for profile in (result_json or {}).get("profiles", []):
                url = profile.get("url")
                if url in profile_urls and profile.get("archived_page_paths") and url not in profile_archives:
                    profile_archives[url] = profile

        match_urls = defaultdict(set)
        for profile_url, profile in profile_archives.items():
            for archive_path in profile.get("archived_page_paths", []):
                archived = storage.read_document(archive_path)
                if not archived:
                    report["errors"].append({"profile_url": profile_url,
                                             "archive_path": archive_path,
                                             "reason": "archive_read_failed"})
                    continue
                for source_id, url in (archived.get("match_urls") or {}).items():
                    if source_id in source_ids:
                        match_urls[source_id].add(str(url))

        point_docs = session.query(
            TennisPointDocument.source_match_id, TennisPointDocument.id,
            TennisPointDocument.parser_version, TennisPointDocument.parse_status,
            TennisPointDocument.available_at, TennisPointDocument.quality_json,
        ).filter(TennisPointDocument.source_match_id.in_(source_ids)).all() if source_ids else []
        docs_by_id = defaultdict(list)
        for doc in point_docs:
            docs_by_id[doc.source_match_id].append(doc)
        jobs_by_id = {job.source_match_id: job for job in session.query(
            TennisPointSourceRefreshJob).filter(
                TennisPointSourceRefreshJob.source_match_id.in_(source_ids),
            ).all()} if source_ids else {}

        to_enqueue = []
        for row in matches:
            candidate_urls = sorted({
                normalized for url in match_urls.get(row.source_match_id, set())
                if (normalized := _source_match_summary_url(row.source_match_id, url))
            })
            url_provenance = "archived_match_link"
            if not candidate_urls:
                observation = latest_observations.get(row.source_match_id)
                payload = (observation.payload or {}) if observation else {}
                home_id, away_id = payload.get("home_player_id"), payload.get("away_player_id")
                home_name, away_name = payload.get("home_name"), payload.get("away_name")
                if home_id and away_id and home_name and away_name:
                    from app.point_history import _match_page_url
                    inferred = _match_page_url(SimpleNamespace(
                        source_match_id=row.source_match_id,
                        source_url=observation.source_url,
                        home_player_id=home_id, away_player_id=away_id,
                        home_name=home_name, away_name=away_name,
                    ))
                    normalized = _source_match_summary_url(row.source_match_id, inferred or "")
                    if normalized:
                        candidate_urls = [normalized]
                        url_provenance = "constructed_by_existing_flashscore_match_url_helper_from_archived_player_ids"
            docs = docs_by_id.get(row.source_match_id, [])
            job = jobs_by_id.get(row.source_match_id)
            latest_doc = max(docs, key=lambda doc: (doc.available_at or datetime.min.replace(tzinfo=timezone.utc), str(doc.id))) if docs else None
            complete = any((doc.quality_json or {}).get("complete") is True for doc in docs)
            active_job = bool(job and job.status in {"queued", "running", "retry"})
            eligible = bool(candidate_urls) and not complete and job is None
            item = {
                "source_match_id": row.source_match_id,
                "tournament": row.tournament,
                "played_at": row.played_at.isoformat() if row.played_at else None,
                "players": [row.winner_name, row.loser_name],
                "gender": row.gender,
                "source_match_urls": candidate_urls,
                "source_url_provenance": url_provenance if candidate_urls else None,
                "existing_point_documents": len(docs),
                "latest_document_quality": (latest_doc.quality_json or {}) if latest_doc else None,
                "existing_job_status": job.status if job else None,
                "enqueue_eligible": eligible,
            }
            report["matches"].append(item)
            if eligible:
                to_enqueue.append((row.source_match_id, candidate_urls[0]))

        if apply and to_enqueue:
            from app.main import _enqueue_source_point_refresh, _flashscore_source_paused
            from app.player_data_coverage import source_capability_enabled
            if not source_capability_enabled("flashscore", "point_by_point"):
                report["apply_blocker"] = "flashscore_point_by_point_capability_disabled"
                to_enqueue = []
            else:
                report["source_paused_until"] = _flashscore_source_paused(session, now=now)
        if apply and to_enqueue:
            from app.main import _enqueue_source_point_refresh
            for source_id, url in to_enqueue:
                _enqueue_source_point_refresh(session, source_id, url, now)
            session.commit()
            report["enqueued_source_matches"] = [source_id for source_id, _ in to_enqueue]
        report["summary"] = {
            "junior_flashscore_matches": len(matches),
            "with_archived_match_url": sum(bool(match_urls.get(row.source_match_id)) for row in matches),
            "existing_point_documents": sum(bool(docs_by_id.get(row.source_match_id)) for row in matches),
            "already_complete": sum(any((doc.quality_json or {}).get("complete") is True
                                        for doc in docs_by_id.get(row.source_match_id, [])) for row in matches),
            "already_active_or_retrying": sum(bool(jobs_by_id.get(row.source_match_id) and
                                        jobs_by_id[row.source_match_id].status in {"queued", "running", "retry"})
                                        for row in matches),
            "eligible_source_only_jobs": len(to_enqueue),
        }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = collect(args.apply)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
