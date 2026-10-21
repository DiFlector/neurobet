"""Append fresh player-metric snapshots after result-fact corrections.

Run inside Research after applying the tennis-result-validity migration:
    python scripts/research/rebuild_tennis_player_metrics.py --apply
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import select

from db.connection import SessionLocal
from db.models import TennisPlayerMetricSnapshot, TennisPlayerProfile, TennisPlayerResultFact
from tennis_adapter.player_metrics import aggregate_results, digest_metrics


def rebuild(apply: bool) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    profiles_updated = 0
    facts_used = 0
    with SessionLocal() as session:
        profiles = session.execute(
            select(TennisPlayerProfile).order_by(TennisPlayerProfile.participant_id)
        ).scalars().all()
        for profile in profiles:
            facts = session.execute(
                select(TennisPlayerResultFact).where(
                    TennisPlayerResultFact.participant_id == profile.participant_id,
                    TennisPlayerResultFact.available_at <= now,
                ).order_by(
                    TennisPlayerResultFact.source_match_id,
                    TennisPlayerResultFact.available_at,
                    TennisPlayerResultFact.created_at,
                )
            ).scalars().all()
            latest_by_match = {}
            for fact in facts:
                current = latest_by_match.get(fact.source_match_id)
                if current is None or (fact.available_at, fact.created_at) > (current.available_at, current.created_at):
                    latest_by_match[fact.source_match_id] = fact
            selected = [fact for fact in latest_by_match.values() if fact.valid]
            metrics = aggregate_results({
                "source_match_id": fact.source_match_id,
                "played_at": fact.played_at,
                "won": fact.won,
                "surface": fact.surface,
                "tour_level": fact.tour_level,
                "sets_played": fact.sets_played,
                "duration_minutes": fact.duration_minutes,
                "serve_points": fact.serve_points,
                "serve_points_won": fact.serve_points_won,
            } for fact in selected)
            digest = digest_metrics(metrics)
            if digest == profile.history_digest:
                continue
            profiles_updated += 1
            facts_used += len(selected)
            if not apply:
                continue
            next_version = int(profile.metrics_version or 0) + 1
            watermark = max((fact.played_at for fact in selected), default=None)
            profile.metrics_version = next_version
            profile.metrics_json = metrics
            profile.history_digest = digest
            profile.history_match_count = int(metrics["overall"]["matches"])
            profile.source_watermark = watermark
            profile.metrics_available_at = now
            session.add(TennisPlayerMetricSnapshot(
                participant_id=profile.participant_id,
                metrics_version=next_version,
                available_at=now,
                source_watermark=watermark,
                history_digest=digest,
                metrics_json=metrics,
            ))
        if apply:
            session.commit()
    return profiles_updated, facts_used


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="append the recalculated snapshots")
    args = parser.parse_args()
    changed, facts = rebuild(args.apply)
    mode = "applied" if args.apply else "dry-run"
    print(f"{mode}: profiles_changed={changed}, current_valid_facts={facts}")

