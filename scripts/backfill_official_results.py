#!/usr/bin/env python3
"""
Backfill official Fonbet results into events.

1. Fetch live https://fon.bet/results feed.
2. Load historical results snapshots from MinIO (fonbet/all/*).
3. Match definitive results to DB events by participant names.
4. Apply EventResultMatcher + settle bets.
5. Demote remaining finished-without-official rows to interrupted
   (they were closed by the old 90s heuristic / cleanup script).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_official_results")


def _fetch_live_results() -> Optional[Dict[str, Any]]:
    urls = [
        "https://clientsapi-lb51.bk6bba-resources.com/results/results.json",
        "https://clientsapi-lb52.bk6bba-resources.ru/results/results.json",
    ]
    for url in urls:
        try:
            proc = subprocess.run(
                [
                    "curl",
                    "-s",
                    "--compressed",
                    "--connect-timeout",
                    "5",
                    "--max-time",
                    "25",
                    url,
                ],
                stdout=subprocess.PIPE,
                timeout=30,
            )
            if proc.returncode == 0 and proc.stdout:
                raw = json.loads(proc.stdout.decode("utf-8", errors="replace"))
                if "events" in raw and "sections" in raw:
                    logger.info(
                        "Live results from %s: events=%d sections=%d",
                        url,
                        len(raw.get("events") or []),
                        len(raw.get("sections") or []),
                    )
                    return raw
        except Exception as exc:
            logger.warning("Live fetch failed for %s: %s", url, exc)
    return None


def _load_minio_day_snapshots(days: Iterable[str]) -> List[Dict[str, Any]]:
    """Load the largest results snapshot per day from MinIO raw-snapshots."""
    try:
        import boto3
        from botocore.client import Config
    except ImportError:
        logger.warning("boto3 unavailable — skipping MinIO historical snapshots")
        return []

    endpoint = os.getenv("MINIO_ENDPOINT") or os.getenv("S3_ENDPOINT") or "http://minio:8000"
    if not endpoint.startswith("http"):
        endpoint = "http://" + endpoint
    access = os.getenv("MINIO_ACCESS_KEY") or os.getenv("S3_ACCESS_KEY") or "neurobet_prod_s3_admin"
    secret = (
        os.getenv("MINIO_SECRET_KEY")
        or os.getenv("S3_SECRET_KEY")
        or "Nb_S3_7kM2wP9rT4vY1bC6eH8jL3nQ5s"
    )
    bucket = os.getenv("MINIO_BUCKET_RAW") or os.getenv("S3_BUCKET") or "raw-snapshots"

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )

    payloads: List[Dict[str, Any]] = []
    for day in days:
        prefix = f"fonbet/all/{day}/"
        best_key = None
        best_size = -1
        token = None
        while True:
            kwargs = {"Bucket": bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kwargs)
            for obj in resp.get("Contents") or []:
                if obj["Size"] > best_size and obj["Key"].endswith(".json"):
                    best_size = obj["Size"]
                    best_key = obj["Key"]
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")

        if not best_key:
            logger.info("No MinIO snapshot for %s", day)
            continue

        body = s3.get_object(Bucket=bucket, Key=best_key)["Body"].read()
        try:
            doc = json.loads(body.decode("utf-8", errors="replace"))
        except Exception as exc:
            logger.warning("Bad JSON %s: %s", best_key, exc)
            continue

        # Collector wraps feed in snapshot envelope.
        payload = doc.get("payload") if isinstance(doc, dict) else None
        if isinstance(payload, dict) and "events" in payload and "sections" in payload:
            feed = payload
        elif isinstance(doc, dict) and "events" in doc and "sections" in doc:
            feed = doc
        else:
            logger.warning("Unrecognized snapshot shape: %s", best_key)
            continue

        logger.info(
            "MinIO %s -> %s (size=%d, events=%d)",
            day,
            best_key,
            best_size,
            len(feed.get("events") or []),
        )
        payloads.append(feed)
    return payloads


def _result_key(result) -> Tuple[str, str, str]:
    from bankroll.settlement import EventResultMatcher

    a = EventResultMatcher.normalize_name(result.participant_a)
    b = EventResultMatcher.normalize_name(result.participant_b)
    if a > b:
        a, b = b, a
    return result.sport_code, a, b


def _result_strength(result) -> Tuple[int, int, int]:
    from bankroll.settlement import EventResultMatcher

    definitive = 1 if EventResultMatcher.result_is_definitive(result) else 0
    sets = EventResultMatcher._tennis_set_total(result.final_score)
    leader = EventResultMatcher._tennis_set_leader(result.final_score)
    return definitive, sets, leader


def merge_results(feeds: List[Dict[str, Any]]):
    from sports_core import FonbetResultsParser
    from bankroll.settlement import EventResultMatcher

    best: Dict[Tuple[str, str, str], Any] = {}
    total_parsed = 0
    for feed in feeds:
        parsed = FonbetResultsParser.parse_all_sports(feed)
        total_parsed += len(parsed)
        for result in parsed:
            if not EventResultMatcher.result_is_definitive(result):
                continue
            key = _result_key(result)
            prev = best.get(key)
            if prev is None or _result_strength(result) >= _result_strength(prev):
                best[key] = result
    logger.info(
        "Merged feeds=%d parsed_rows=%d definitive_unique=%d",
        len(feeds),
        total_parsed,
        len(best),
    )
    return list(best.values())


def apply_results(results) -> Dict[str, int]:
    from db.connection import SessionLocal
    from bankroll.settlement import EventResultMatcher, BetSettlementEngine

    stats = defaultdict(int)
    with SessionLocal() as session:
        for result in results:
            matched = EventResultMatcher.match_and_complete_event(session, result)
            if not matched:
                stats["unmatched"] += 1
                continue
            stats["matched"] += 1
            if EventResultMatcher.result_is_definitive(result) and matched.status in (
                "finished",
                "cancelled",
            ):
                stats["finished"] += 1
                settlements = BetSettlementEngine.settle_event_bets(session, matched, result)
                stats["settlements"] += len(settlements)
        session.commit()
    return dict(stats)


def demote_unverified_finished() -> int:
    """
    Rows marked finished without an official Fonbet result are not trustworthy
    (old 90s heuristic / cleanup script). Move them to interrupted and keep score.
    """
    from db.connection import SessionLocal
    from db.models.events import Event
    from sqlalchemy import select

    demoted = 0
    with SessionLocal() as session:
        rows = session.execute(
            select(Event).where(
                Event.source == "fonbet",
                Event.status == "finished",
            )
        ).scalars().all()
        now = datetime.now(timezone.utc).isoformat()
        from bankroll.settlement import EventResultMatcher

        for event in rows:
            meta = dict(event.metadata_json or {})
            official = EventResultMatcher._canonical_from_metadata(
                meta.get("fonbet_official_result")
            )
            has_final = (
                isinstance(event.current_score, dict)
                and "final_score" in event.current_score
            )
            if official and EventResultMatcher.result_is_definitive(official):
                # Repair score envelope if official exists but current_score was wiped.
                if not has_final:
                    event.current_score = {
                        "final_score": official.final_score,
                        "winner": official.winner,
                        "status": official.status,
                        "is_retired": official.is_retired,
                        "is_walkover": official.is_walkover,
                    }
                continue
            event.status = "interrupted"
            event.is_live = False
            event.finished_at = None
            meta["backfill_demoted_unverified_finished"] = True
            meta["backfill_demoted_at"] = now
            meta["backfill_demote_reason"] = (
                "Finished without definitive fonbet_official_result; "
                "awaiting or missing from results feed"
            )
            event.metadata_json = meta
            demoted += 1
        session.commit()
    return demoted


def clear_premature_official_on_live() -> int:
    """Drop mid-match official markers left on live/interrupted rows."""
    from db.connection import SessionLocal
    from db.models.events import Event
    from bankroll.settlement import EventResultMatcher
    from sqlalchemy import select

    cleared = 0
    with SessionLocal() as session:
        rows = session.execute(
            select(Event).where(
                Event.source == "fonbet",
                Event.status.in_(("live", "interrupted")),
            )
        ).scalars().all()
        for event in rows:
            meta = dict(event.metadata_json or {})
            official = EventResultMatcher._canonical_from_metadata(meta.get("fonbet_official_result"))
            if official and EventResultMatcher.result_is_definitive(official):
                continue
            changed = False
            for key in (
                "fonbet_official_result",
                "pending_official_result",
                "settlement_review_required",
                "settlement_review_reason",
            ):
                if key in meta:
                    meta.pop(key, None)
                    changed = True
            if changed:
                event.metadata_json = meta
                # If a weak official marked finished fields somehow, keep interrupted/live.
                if event.status == "live":
                    event.finished_at = None
                cleared += 1
        session.commit()
    return cleared


def summarize() -> Dict[str, Any]:
    from db.connection import SessionLocal
    from db.models.events import Event
    from sqlalchemy import select, func, case, literal_column, text

    with SessionLocal() as session:
        rows = session.execute(
            text(
                """
                SELECT status, is_live,
                       COUNT(*) AS n,
                       COUNT(*) FILTER (WHERE current_score ? 'final_score') AS with_final,
                       COUNT(*) FILTER (WHERE metadata_json ? 'fonbet_official_result') AS with_official
                FROM events
                GROUP BY status, is_live
                ORDER BY n DESC
                """
            )
        ).mappings().all()
        return [dict(r) for r in rows]


def main() -> int:
    live = _fetch_live_results()
    feeds: List[Dict[str, Any]] = []
    if live:
        feeds.append(live)

    # Cover the window where unverified finished rows were created.
    days = [
        "2026-10-08",
        "2026-10-09",
        "2026-10-10",
        "2026-10-11",
        "2026-10-12",
        "2026-10-13",
    ]
    feeds.extend(_load_minio_day_snapshots(days))
    if not feeds:
        logger.error("No results feeds available")
        return 1

    results = merge_results(feeds)
    apply_stats = apply_results(results)
    logger.info("Apply stats: %s", apply_stats)

    cleared = clear_premature_official_on_live()
    logger.info("Cleared premature official markers on live/interrupted: %d", cleared)

    demoted = demote_unverified_finished()
    logger.info("Demoted unverified finished -> interrupted: %d", demoted)

    # Re-apply once more: demotion may have freed name collisions; historical
    # results can now attach to interrupted rows that previously looked finished.
    apply_stats_2 = apply_results(results)
    logger.info("Re-apply after demote: %s", apply_stats_2)

    logger.info("Summary: %s", summarize())
    return 0


if __name__ == "__main__":
    sys.exit(main())
