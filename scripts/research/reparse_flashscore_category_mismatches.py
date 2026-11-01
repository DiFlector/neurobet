"""Review and optionally reparse archived Flashscore category mismatch profiles.

Only saved profile feed archives are read. The default mode is a dry run; --apply
uses the normal append-only Flashscore persistence path with parser v10.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from db.connection import SessionLocal
from db.models.external_tennis import (
    ExtTennisMatch, ExtTennisMatchObservation, ExtTennisPlayer, FlashscoreImportJob,
)
from app.storage import ResearchStorage
from app.tennis_flashscore import (
    PARSER_VERSION, FlashscoreTennisImporter, ScrapeResult, _feed_records,
    _observation_hash, _parse_profile_feeds,
)


def collect(apply: bool = False) -> dict:
    storage = ResearchStorage()
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "parser_version": PARSER_VERSION, "dry_run": not apply,
              "profiles": [], "reviewed_rows": [], "errors": []}
    with SessionLocal() as session:
        audit_path = Path(__file__).parents[2] / "docs/research/tennis_category_snapshot_audit_2026-10-25.json"
        previous_audit = json.loads(audit_path.read_text(encoding="utf-8"))
        target_ids = {row["source_match_id"] for row in previous_audit.get(
            "flashscore_gender_mismatch_examples", [])}
        targets = session.query(ExtTennisMatch).filter(
            ExtTennisMatch.source == "flashscore",
            ExtTennisMatch.source_match_id.in_(target_ids),
        ).all()
        targets_by_id = {row.source_match_id: row for row in targets}
        rows = session.query(
            ExtTennisMatchObservation.source_match_id,
            ExtTennisMatchObservation.available_at,
            ExtTennisMatchObservation.source_url,
            ExtTennisMatchObservation.source_content_hash,
        ).filter(
            ExtTennisMatchObservation.source == "flashscore",
            ExtTennisMatchObservation.source_match_id.in_(target_ids),
        ).order_by(ExtTennisMatchObservation.available_at.desc()).all()
        urls_by_id = defaultdict(set)
        latest_source_observation = {}
        for source_id, available_at, source_url, content_hash in rows:
            urls_by_id[source_id].add(source_url)
            latest_source_observation.setdefault(source_id, (available_at, source_url, content_hash))
        urls = {url for source_urls in urls_by_id.values() for url in source_urls}
        player_ids = {match.group(1) for url in urls
                      if (match := re.search(r"/([A-Za-z0-9]{8})/results", url))}
        names = {row.source_player_id: row.full_name for row in session.query(ExtTennisPlayer).filter(
            ExtTennisPlayer.source == "flashscore",
            ExtTennisPlayer.source_player_id.in_(player_ids),
        ).all()}
        jobs = session.query(FlashscoreImportJob).filter(
            FlashscoreImportJob.status == "completed",
        ).order_by(FlashscoreImportJob.updated_at.desc()).all()
        archived_profile = {}
        for job in jobs:
            for profile in (job.result_json or {}).get("profiles", []):
                url = profile.get("url")
                if url in urls and url not in archived_profile and profile.get("archived_page_paths"):
                    archived_profile[url] = profile

        parsed_ids = set()
        raw_reviewed = {}
        parsed_items_by_id = {}
        for url in sorted(urls):
            archived_profile_info = archived_profile.get(url)
            if not archived_profile_info:
                report["errors"].append({"url": url, "reason": "no_completed_archived_profile"})
                continue
            payloads = []
            archive_path_by_feed = {}
            missing_pages = []
            for path in archived_profile_info.get("archived_page_paths", []):
                archived = storage.read_document(path)
                if not archived:
                    missing_pages.append(path)
                    continue
                for feed in (archived.get("result_feeds") or []):
                    if isinstance(feed, str) and feed:
                        payloads.append(feed)
                        archive_path_by_feed[feed] = path
            if missing_pages or not payloads:
                report["errors"].append({"url": url, "reason": "archive_incomplete_or_empty",
                                          "missing_pages": missing_pages, "feeds": len(payloads)})
                continue
            player_id = str(archived_profile_info.get("player_id") or "")
            full_name = names.get(player_id)
            for payload in payloads:
                for fields, context in _feed_records(payload)[0]:
                    source_id = str(fields.get("AA") or "")
                    if source_id not in target_ids:
                        continue
                    raw_reviewed[source_id] = {
                        "source_match_id": source_id,
                        "archived_tournament": str(context.get("ZK") or context.get("ZA", "").split(":", 1)[-1]).strip(),
                        "raw_home_name": fields.get("CX"), "raw_away_name": fields.get("AF"),
                        "raw_category_code": fields.get("ZL"), "raw_source_profile": url,
                        "archived_document_path": archive_path_by_feed.get(payload),
                    }
            try:
                profile = _parse_profile_feeds(
                    payloads, url, full_name, pagination_complete=True,
                    history_scope="full_accessible", pagination_reason="archived_profile_reparse",
                )
            except Exception as exc:  # report per source profile; do not abort the batch
                report["errors"].append({"url": url, "reason": f"parse_error:{type(exc).__name__}:{exc}"})
                continue
            matched = [item for item in profile.matches if item.source_match_id in target_ids]
            report["profiles"].append({"url": url, "player_id": player_id,
                "archived_parser_version": archived_profile_info.get("parser_version"),
                "archived_pages": len(archived_profile_info.get("archived_page_paths", [])),
                "feeds": len(payloads), "parsed_matches": len(profile.matches),
                "target_matches": len(matched), "duplicate_source_rows": profile.duplicate_source_rows,
                "pagination_complete": archived_profile_info.get("pagination_complete")})
            for item in matched:
                parsed_ids.add(item.source_match_id)
                parsed_items_by_id[item.source_match_id] = item
                current = targets_by_id[item.source_match_id]
                report["reviewed_rows"].append({
                    "source_match_id": item.source_match_id,
                    "archived_tournament": item.tournament,
                    "archived_gender": item.gender,
                    "archived_tour_level": item.tour_level,
                    "archived_players": [item.home_name, item.away_name],
                    "materialized_tournament": current.tournament,
                    "materialized_gender": current.gender,
                    "materialized_tour_level": current.tour_level,
                    "source_category_marker": "ATP Cup" if "ATP Cup" in item.tournament else "M15",
                    "source_url": url,
                })
            target_singles = [item for item in matched if item.gender == "male" and item.tour_level == "itf"]
            if apply and target_singles:
                scoped_profile = replace(profile, matches=tuple(target_singles), rejected_source_matches=())
                result = ScrapeResult(
                    status="available", url=url, retrieved_at=datetime.now(timezone.utc),
                    content_hash=str(archived_profile_info.get("content_hash") or ""),
                    profile=scoped_profile, archived_path=archived_profile_info.get("archived_path"),
                    archived_page_paths=tuple(archived_profile_info.get("archived_page_paths", [])),
                )
                imported = FlashscoreTennisImporter._persist_result(result)
                report["profiles"][-1]["matches_added"] = imported

        # Some profile imports archived the exact raw profile response in
        # MinIO without a completed FlashscoreImportJob pointer. Resolve those
        # content-addressed objects from the observation's profile/date/hash.
        object_client = storage._get_client()
        visited_object_keys = set()
        for unresolved_id in sorted(target_ids - set(raw_reviewed)):
            observation = latest_source_observation.get(unresolved_id)
            if not observation or not object_client:
                continue
            available_at, url, content_hash = observation
            profile_match = re.search(r"/([A-Za-z0-9]{8})/results", url or "")
            digest = str(content_hash or "").removeprefix("sha256:")
            if not profile_match or not re.fullmatch(r"[0-9a-f]{64}", digest):
                continue
            prefix = f"snapshots/research/{available_at.date()}/{profile_match.group(1)}/{digest}"
            try:
                listed = object_client.list_objects_v2(
                    Bucket=storage.bucket, Prefix=prefix, MaxKeys=20,
                )
            except Exception as exc:
                report["errors"].append({"source_match_id": unresolved_id,
                    "reason": f"archive_list_failed:{type(exc).__name__}"})
                continue
            keys = [item["Key"] for item in listed.get("Contents", [])]
            if not keys:
                continue
            archive_path = keys[0]
            if archive_path in visited_object_keys:
                continue
            visited_object_keys.add(archive_path)
            archived = storage.read_document(archive_path)
            payloads = [item for item in (archived or {}).get("result_feeds", [])
                        if isinstance(item, str) and item]
            if not payloads:
                report["errors"].append({"source_match_id": unresolved_id,
                    "archive_path": archive_path, "reason": "saved_profile_archive_has_no_result_feeds"})
                continue
            for feed in payloads:
                for fields, context in _feed_records(feed)[0]:
                    source_id = str(fields.get("AA") or "")
                    if source_id not in target_ids:
                        continue
                    raw_reviewed[source_id] = {
                        "source_match_id": source_id,
                        "archived_tournament": str(context.get("ZK") or context.get("ZA", "").split(":", 1)[-1]).strip(),
                        "raw_home_name": fields.get("CX"), "raw_away_name": fields.get("AF"),
                        "raw_category_code": fields.get("ZL"), "raw_source_profile": url,
                        "archived_document_path": archive_path,
                    }
            player_id = profile_match.group(1)
            try:
                profile = _parse_profile_feeds(
                    payloads, url, names.get(player_id),
                    pagination_complete=bool(archived.get("pagination_complete")),
                    history_scope=str(archived.get("history_scope") or "partial"),
                    pagination_reason="content_addressed_archive_reparse",
                )
            except Exception as exc:
                report["errors"].append({"source_match_id": unresolved_id,
                    "archive_path": archive_path,
                    "reason": f"fallback_parse_error:{type(exc).__name__}:{exc}"})
                continue
            matches = [item for item in profile.matches if item.source_match_id in target_ids]
            report["profiles"].append({"url": url, "player_id": player_id,
                "archived_parser_version": archived.get("parser_version"),
                "archived_pages": 1, "feeds": len(payloads),
                "parsed_matches": len(profile.matches), "target_matches": len(matches),
                "archive_recovery": "content_addressed_minio_object"})
            for item in matches:
                parsed_ids.add(item.source_match_id)
                parsed_items_by_id[item.source_match_id] = item
                current = targets_by_id[item.source_match_id]
                report["reviewed_rows"].append({
                    "source_match_id": item.source_match_id,
                    "archived_tournament": item.tournament,
                    "archived_gender": item.gender,
                    "archived_tour_level": item.tour_level,
                    "archived_players": [item.home_name, item.away_name],
                    "materialized_tournament": current.tournament,
                    "materialized_gender": current.gender,
                    "materialized_tour_level": current.tour_level,
                    "source_category_marker": "ATP Cup" if "ATP Cup" in item.tournament else "M15",
                    "source_url": url,
                    "archived_document_path": archive_path,
                })
            target_singles = [item for item in matches if item.gender == "male" and item.tour_level == "itf"]
            if apply and target_singles:
                scoped_profile = replace(profile, matches=tuple(target_singles), rejected_source_matches=())
                result = ScrapeResult(
                    status="available", url=url, retrieved_at=datetime.now(timezone.utc),
                    content_hash=str(archived.get("content_hash") or digest),
                    profile=scoped_profile, archived_path=archive_path,
                    archived_page_paths=(archive_path,),
                )
                imported = FlashscoreTennisImporter._persist_result(result)
                report["profiles"][-1]["matches_added"] = imported

        reviewed_urls = {item.get("raw_source_profile") for item in raw_reviewed.values()}
        report["errors"] = [error for error in report["errors"]
                             if error.get("url") not in reviewed_urls]
        by_id = {row["source_match_id"]: row for row in report["reviewed_rows"]}
        for source_id, source_evidence in raw_reviewed.items():
            current = targets_by_id.get(source_id)
            if current is None:
                continue
            parsed = by_id.get(source_id, {})
            report["reviewed_rows"].append({
                **source_evidence,
                "source_category_marker": "ATP Cup" if "ATP Cup" in source_evidence["archived_tournament"] else "M15",
                "archived_gender": parsed.get("archived_gender"),
                "archived_tour_level": parsed.get("archived_tour_level"),
                "materialized_tournament": current.tournament,
                "materialized_gender": current.gender,
                "materialized_tour_level": current.tour_level,
                "parser_v10_included_as_singles": source_id in parsed_ids,
            })
        if apply:
            # ATP Cup rows are historical doubles rows and are intentionally
            # excluded from the singles profile parser. Preserve the existing
            # source fact, but version its explicit tournament classification.
            corrected_atp_cup = 0
            for source_id, evidence in raw_reviewed.items():
                if "ATP Cup" not in evidence["archived_tournament"]:
                    continue
                current = targets_by_id.get(source_id)
                if current is None:
                    continue
                latest = session.query(ExtTennisMatchObservation).filter_by(
                    source="flashscore", source_match_id=source_id,
                ).order_by(ExtTennisMatchObservation.available_at.desc()).first()
                payload = dict(latest.payload or {}) if latest else {}
                payload.update({
                    "tournament": evidence["archived_tournament"],
                    "gender": "male", "tour_level": "atp",
                    "parser_version": PARSER_VERSION,
                    "category_evidence": "archived_flashscore_tournament_marker:ATP Cup",
                    "source_url": evidence["raw_source_profile"],
                })
                digest = _observation_hash(payload)
                if latest and latest.parser_version == PARSER_VERSION and latest.observation_hash == digest:
                    continue
                session.add(ExtTennisMatchObservation(
                    source="flashscore", source_match_id=source_id,
                    observation_hash=digest, available_at=datetime.now(timezone.utc),
                    source_url=evidence["raw_source_profile"],
                    source_content_hash=str(archived_profile.get(evidence["raw_source_profile"], {}).get("content_hash") or "") or None,
                    parser_version=PARSER_VERSION, payload=payload,
                ))
                current.gender = "male"
                current.tour_level = "atp"
                current.stats = {**(current.stats or {}), "parser_version": PARSER_VERSION,
                                 "category_evidence": "archived_flashscore_tournament_marker:ATP Cup",
                                 "source_url": evidence["raw_source_profile"]}
                corrected_atp_cup += 1
            report["atp_cup_rows_corrected"] = corrected_atp_cup
            session.commit()
        # Deduplicate target display rows: a source ID can appear in the two
        # participant profiles' saved feeds.
        deduped = {}
        for row in report["reviewed_rows"]:
            deduped[row["source_match_id"]] = row
        report["reviewed_rows"] = [deduped[key] for key in sorted(deduped)]
        report["target_rows"] = len(target_ids)
        report["raw_archive_rows_reviewed"] = len(raw_reviewed)
        report["archived_source_pages_reparsed"] = len(parsed_ids)
        report["target_rows_not_found_in_archives"] = sorted(target_ids - set(raw_reviewed))
        report["archive_rows_excluded_from_singles_parser"] = sorted(set(raw_reviewed) - parsed_ids)
        if apply:
            state = session.query(ExtTennisMatch.source_match_id, ExtTennisMatch.tournament,
                                  ExtTennisMatch.gender, ExtTennisMatch.tour_level,
                                  ExtTennisMatch.stats).filter(
                ExtTennisMatch.source == "flashscore",
                ExtTennisMatch.source_match_id.in_(target_ids),
            ).all()
            report["production_projection_after_apply"] = {
                "rows": len(state),
                "parser_v10": sum((row.stats or {}).get("parser_version") == PARSER_VERSION for row in state),
                "male": sum(row.gender == "male" for row in state),
                "remaining_gender_mismatches": sorted(
                    row.source_match_id for row in state
                    if "M15" in row.tournament and row.gender != "male"
                    or "ATP Cup" in row.tournament and row.gender != "male"
                ),
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
