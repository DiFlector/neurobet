"""Read point-in-time materialized tennis-player metrics."""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select


PLAYER_HISTORY_MAX_AGE = timedelta(days=7)


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def _latest_confirmed_flashscore_link(link_rows):
    """Select the latest decision per source ID, then the newest confirmed ID.

    A bulk identity review can write one confirmed ID and several rejected
    alternatives at the same timestamp. Those rejections do not revoke the
    distinct confirmed ID; only a newer decision for that same source ID does.
    """
    latest_by_source_id = {}
    for row in link_rows or ():
        latest_by_source_id.setdefault(row.source_player_id, row)
    return next((row for row in latest_by_source_id.values() if row.status == "confirmed"), None)


def _latest_confirmed_flashscore_id(link_rows) -> Optional[str]:
    link = _latest_confirmed_flashscore_link(link_rows)
    return link.source_player_id if link else None


def _profile_identity_matches(profile, source_player_id: Optional[str]) -> bool:
    """Reject cached metrics when their stored source identity conflicts with the confirmed link."""
    if not source_player_id:
        return False
    stored_id = (profile.source_identities or {}).get("flashscore")
    return stored_id is None or stored_id == source_player_id


def load_player_metric(session, participant_id, cutoff: datetime) -> Optional[dict[str, Any]]:
    if participant_id is None:
        return None
    from db.models import TennisPlayerIdentityLink, TennisPlayerMetricSnapshot, TennisPlayerProfile
    from db.models.external_tennis import ExtTennisCanonicalPlayerAlias, TennisCanonicalMetricSnapshot, TennisPlayerRankingSnapshot
    from tennis_adapter.player_metrics import metrics_as_of

    profile = session.get(TennisPlayerProfile, participant_id)
    if profile is None:
        return None
    link_rows = session.execute(
        select(TennisPlayerIdentityLink).where(
            TennisPlayerIdentityLink.participant_id == participant_id,
            TennisPlayerIdentityLink.source == "flashscore",
            TennisPlayerIdentityLink.available_at <= cutoff,
        ).order_by(TennisPlayerIdentityLink.available_at.desc(), TennisPlayerIdentityLink.id.desc())
    ).scalars().all()
    # The newest identity decision is authoritative. Skipping a newer
    # revocation and finding an older confirmation would resurrect a stale
    # source link in point-in-time reads.
    confirmed_link = _latest_confirmed_flashscore_link(link_rows)
    confirmed_source_id = confirmed_link.source_player_id if confirmed_link else None
    confirmed_identity_at = confirmed_link.available_at if confirmed_link else None
    identities = profile.source_identities or {}
    if not confirmed_source_id and (
        identities.get("flashscore") or identities.get("flashscore_candidates") or profile.profile_url
    ):
        # Historical automatic name matches were not identity evidence. Keep
        # their cached metrics out of the forecast until a manual confirmation
        # creates a point-in-time identity link.
        return None
    metric_profiles = [profile]
    if confirmed_source_id:
        # Fonbet may expose ordering/initial variants as separate participant
        # rows. A shared Flashscore ID is the identity evidence that lets them
        # read the same cached history without merging the source events.
        linked_participant_ids = session.execute(
            select(TennisPlayerIdentityLink.participant_id).where(
                TennisPlayerIdentityLink.source == "flashscore",
                TennisPlayerIdentityLink.source_player_id == confirmed_source_id,
                TennisPlayerIdentityLink.available_at <= cutoff,
            ).distinct()
        ).scalars().all()
        linked_rows = session.execute(
            select(TennisPlayerIdentityLink).where(
                TennisPlayerIdentityLink.source == "flashscore",
                TennisPlayerIdentityLink.participant_id.in_(linked_participant_ids),
                TennisPlayerIdentityLink.available_at <= cutoff,
            ).order_by(TennisPlayerIdentityLink.participant_id,
                       TennisPlayerIdentityLink.available_at.desc(), TennisPlayerIdentityLink.id.desc())
        ).scalars().all()
        latest_link_by_participant = {}
        for row in linked_rows:
            latest_link_by_participant.setdefault(row.participant_id, row)
        confirmed_ids = [
            pid for pid, row in latest_link_by_participant.items()
            if row.status == "confirmed" and row.source_player_id == confirmed_source_id
        ]
        metric_profiles = session.execute(
            select(TennisPlayerProfile).where(TennisPlayerProfile.participant_id.in_(confirmed_ids))
        ).scalars().all() or [profile]
    metric_profile_ids = [row.participant_id for row in metric_profiles]
    snapshot_query = select(TennisPlayerMetricSnapshot).where(
        TennisPlayerMetricSnapshot.participant_id.in_(metric_profile_ids),
        TennisPlayerMetricSnapshot.available_at <= cutoff,
    )
    if confirmed_source_id:
        snapshot_query = snapshot_query.where(
            TennisPlayerMetricSnapshot.source_player_id == confirmed_source_id
        )
    else:
        snapshot_query = snapshot_query.where(TennisPlayerMetricSnapshot.source_player_id.is_(None))
    snapshot = session.execute(
        snapshot_query
        .order_by(TennisPlayerMetricSnapshot.available_at.desc(), TennisPlayerMetricSnapshot.metrics_version.desc())
        .limit(1)
    ).scalars().first()
    metric_owner = next((row for row in metric_profiles if snapshot and row.participant_id == snapshot.participant_id), None)
    if snapshot is None:
        # Some profiles were materialized before their confirmed Flashscore
        # link existed, leaving the immutable snapshot without a source ID.
        # Use the current aggregate only after the matching identity decision
        # and only if the aggregate itself names the same source identity.
        eligible_profiles = [
            row for row in metric_profiles
            if row.metrics_available_at
            and row.metrics_available_at <= cutoff
            and _profile_identity_matches(row, confirmed_source_id)
            and (confirmed_identity_at is None or row.metrics_available_at >= confirmed_identity_at)
        ]
        metric_owner = max(eligible_profiles, key=lambda row: row.metrics_available_at, default=None)
    else:
        metric_owner = metric_owner or profile
    metrics = snapshot.metrics_json if snapshot else (metric_owner.metrics_json if metric_owner else None)
    if snapshot is None and metric_owner is not None:
        if not _profile_identity_matches(metric_owner, confirmed_source_id):
            return None
    if metrics is None:
        return None
    available_at = snapshot.available_at if snapshot else metric_owner.metrics_available_at
    profile_metrics_fresh = available_at is not None and _utc(cutoff) - _utc(available_at) <= PLAYER_HISTORY_MAX_AGE
    canonical_player_id = None
    if confirmed_source_id:
        aliases = session.execute(
            select(ExtTennisCanonicalPlayerAlias).where(
                ExtTennisCanonicalPlayerAlias.source == "flashscore",
                ExtTennisCanonicalPlayerAlias.source_player_id == confirmed_source_id,
                ExtTennisCanonicalPlayerAlias.status == "confirmed",
                ExtTennisCanonicalPlayerAlias.created_at <= cutoff,
            ).order_by(ExtTennisCanonicalPlayerAlias.created_at.desc(), ExtTennisCanonicalPlayerAlias.id.desc())
        ).scalars().all()
        if aliases:
            canonical_player_id = aliases[0].canonical_player_id
    canonical_snapshot = None
    ranking = None
    if canonical_player_id is not None:
        canonical_snapshot = session.execute(
            select(TennisCanonicalMetricSnapshot).where(
                TennisCanonicalMetricSnapshot.canonical_player_id == canonical_player_id,
                TennisCanonicalMetricSnapshot.available_at <= cutoff,
            ).order_by(TennisCanonicalMetricSnapshot.available_at.desc(),
                       TennisCanonicalMetricSnapshot.metrics_version.desc()).limit(1)
        ).scalars().first()
        if canonical_snapshot is not None:
            gender = str((canonical_snapshot.metrics_json or {}).get("gender", "")).lower()
            tour = "wta" if gender in {"f", "w", "female"} else "atp" if gender in {"m", "male"} else None
            if tour:
                ranking = session.execute(
                select(TennisPlayerRankingSnapshot).where(
                    TennisPlayerRankingSnapshot.canonical_player_id == canonical_player_id,
                    TennisPlayerRankingSnapshot.source.in_(
                        ("atp_official", "wta_official", "live_tennis_api_official")
                    ),
                    TennisPlayerRankingSnapshot.tour == tour,
                    TennisPlayerRankingSnapshot.is_singles.is_(True),
                    TennisPlayerRankingSnapshot.available_at <= cutoff,
                    TennisPlayerRankingSnapshot.available_at >= cutoff - timedelta(days=30),
                ).order_by(TennisPlayerRankingSnapshot.available_at.desc(),
                           TennisPlayerRankingSnapshot.ranking_date.desc().nullslast()).limit(1)
                ).scalars().first()
    if not profile_metrics_fresh and canonical_snapshot is None:
        return None
    return {
        "participant_id": str(participant_id),
        "metric_snapshot_id": str(snapshot.id) if snapshot else None,
        "source_player_id": confirmed_source_id,
        "identity_available_at": confirmed_identity_at.isoformat() if confirmed_identity_at else None,
        "metrics_version": snapshot.metrics_version if snapshot else metric_owner.metrics_version,
        "available_at": available_at.isoformat(),
        "source_watermark": (snapshot.source_watermark if snapshot else metric_owner.source_watermark).isoformat() if (snapshot.source_watermark if snapshot else metric_owner.source_watermark) else None,
        "history_digest": snapshot.history_digest if snapshot else metric_owner.history_digest,
        "sync_status": profile.sync_status,
        "sync_error": profile.sync_error,
        "profile_url": metric_owner.profile_url,
        "metrics": metrics_as_of(metrics, cutoff) if profile_metrics_fresh else {},
        "canonical_player_id": str(canonical_player_id) if canonical_player_id else None,
        "canonical_metric_snapshot_id": str(canonical_snapshot.id) if canonical_snapshot else None,
        "canonical_metrics": canonical_snapshot.metrics_json if canonical_snapshot else {},
        "canonical_metrics_digest": canonical_snapshot.history_digest if canonical_snapshot else None,
        "canonical_metrics_available_at": canonical_snapshot.available_at.isoformat() if canonical_snapshot else None,
        "ranking": ({
            "tour": ranking.tour, "rank": ranking.rank, "points": ranking.points,
            "ranking_date": ranking.ranking_date.isoformat() if ranking.ranking_date else None,
            "available_at": ranking.available_at.isoformat(), "source": ranking.source,
            "source_ref": ranking.source_ref, "source_content_hash": ranking.source_content_hash,
            "source_url": ranking.source_url, "parser_version": ranking.parser_version,
            "ranking_status": ranking.ranking_status or "ranked",
        } if ranking and ranking.rank is not None
          and ranking.ranking_status in (None, "ranked") else None),
    }


def pair_features(left: Optional[dict[str, Any]], right: Optional[dict[str, Any]], surface: str = "unknown") -> dict[str, Any]:
    """Build the auditable pair vector from already-materialized player rows."""
    def value(row, *keys, default=0.0):
        current = (row or {}).get("metrics", {})
        for key in keys:
            current = current.get(key, {}) if isinstance(current, dict) else {}
        return current if isinstance(current, (int, float)) else default

    left_metrics = (left or {}).get("metrics", {})
    right_metrics = (right or {}).get("metrics", {})
    independent_a = (left or {}).get("canonical_metrics", {})
    independent_b = (right or {}).get("canonical_metrics", {})
    ranking_a = (left or {}).get("ranking") or {}
    ranking_b = (right or {}).get("ranking") or {}
    canonical_a = str((left or {}).get("canonical_player_id") or "")
    canonical_b = str((right or {}).get("canonical_player_id") or "")
    h2h_a = (independent_a.get("head_to_head") or {}).get(canonical_b) if canonical_b else None
    h2h_b = (independent_b.get("head_to_head") or {}).get(canonical_a) if canonical_a else None
    h2h_consistent = bool(
        h2h_a and h2h_b
        and int(h2h_a.get("matches", -1)) == int(h2h_b.get("matches", -2))
        and int(h2h_a.get("wins", -1)) == int(h2h_b.get("losses", -2))
        and int(h2h_a.get("losses", -1)) == int(h2h_b.get("wins", -2))
    )
    elo_a = value(left, "elo", "overall", default=1500.0)
    elo_b = value(right, "elo", "overall", default=1500.0)
    recent_a = independent_a.get("recent", {}).get("days_90") or {"matches": 0, "wins": 0, "win_rate": None}
    recent_b = independent_b.get("recent", {}).get("days_90") or {"matches": 0, "wins": 0, "win_rate": None}
    surface_key = str(surface or "unknown").strip().casefold()
    surface_recent_a = (independent_a.get("recent_by_surface") or {}).get(surface_key, {})
    surface_recent_b = (independent_b.get("recent_by_surface") or {}).get(surface_key, {})
    surface_a = recent_a.get("win_rate") if recent_a.get("win_rate") is not None else 0.5
    surface_b = recent_b.get("win_rate") if recent_b.get("win_rate") is not None else 0.5
    return {
        "elo_a": elo_a,
        "elo_b": elo_b,
        "elo_diff": elo_a - elo_b,
        "win_rate_90d_a": surface_a,
        "win_rate_90d_b": surface_b,
        "win_rate_90d_diff": surface_a - surface_b,
        "match_count_a": value(left, "overall", "matches"),
        "match_count_b": value(right, "overall", "matches"),
        "player_metrics_missing_a": left is None,
        "player_metrics_missing_b": right is None,
        "independent_elo_a": float((independent_a.get("elo") or {}).get("overall", 1500.0)),
        "independent_elo_b": float((independent_b.get("elo") or {}).get("overall", 1500.0)),
        "independent_surface_elo_a": float((independent_a.get("elo") or {}).get("by_surface", {}).get(str(surface or "unknown").lower(), (independent_a.get("elo") or {}).get("overall", 1500.0))),
        "independent_surface_elo_b": float((independent_b.get("elo") or {}).get("by_surface", {}).get(str(surface or "unknown").lower(), (independent_b.get("elo") or {}).get("overall", 1500.0))),
        "strength_elo_a": float((independent_a.get("elo") or {}).get("by_surface", {}).get(str(surface or "unknown").lower(), (independent_a.get("elo") or {}).get("overall", 1500.0))) if str(surface or "unknown").lower() in {"hard", "clay", "grass", "carpet"} else float((independent_a.get("elo") or {}).get("overall", 1500.0)),
        "strength_elo_b": float((independent_b.get("elo") or {}).get("by_surface", {}).get(str(surface or "unknown").lower(), (independent_b.get("elo") or {}).get("overall", 1500.0))) if str(surface or "unknown").lower() in {"hard", "clay", "grass", "carpet"} else float((independent_b.get("elo") or {}).get("overall", 1500.0)),
        "strength_elo_surface_used": str(surface or "unknown").lower() in {"hard", "clay", "grass", "carpet"} and str(surface or "unknown").lower() in (independent_a.get("elo") or {}).get("by_surface", {}) and str(surface or "unknown").lower() in (independent_b.get("elo") or {}).get("by_surface", {}),
        "independent_glicko_a": float((independent_a.get("glicko") or {}).get("rating", 1500.0)),
        "independent_glicko_b": float((independent_b.get("glicko") or {}).get("rating", 1500.0)),
        "independent_rd_a": float((independent_a.get("glicko") or {}).get("rd", 350.0)),
        "independent_rd_b": float((independent_b.get("glicko") or {}).get("rd", 350.0)),
        "independent_matches_a": int((independent_a.get("overall") or {}).get("matches", 0)),
        "independent_matches_b": int((independent_b.get("overall") or {}).get("matches", 0)),
        "rolling_90d_a": surface_a,
        "rolling_90d_b": surface_b,
        "rolling_90d_matches_a": int((left_metrics.get("recent", {}).get("days_90") or {}).get("matches", 0)),
        "rolling_90d_matches_b": int((right_metrics.get("recent", {}).get("days_90") or {}).get("matches", 0)),
        "rolling_90d_wins_a": int((left_metrics.get("recent", {}).get("days_90") or {}).get("wins", 0)),
        "rolling_90d_wins_b": int((right_metrics.get("recent", {}).get("days_90") or {}).get("wins", 0)),
        "rolling_90d_summary_a": recent_a,
        "rolling_90d_summary_b": recent_b,
        "surface_form_a": surface_recent_a,
        "surface_form_b": surface_recent_b,
        "surface_form_key": surface_key,
        "tour_rank_a": ranking_a.get("rank"),
        "tour_rank_b": ranking_b.get("rank"),
        "tour_rank_available_a": ranking_a.get("available_at"),
        "tour_rank_available_b": ranking_b.get("available_at"),
        "ranking_comparable": bool(ranking_a.get("tour") and ranking_a.get("tour") == ranking_b.get("tour")),
        "h2h_available": h2h_consistent,
        "h2h_matches": int(h2h_a.get("matches", 0)) if h2h_consistent else 0,
        "h2h_wins_a": int(h2h_a.get("wins", 0)) if h2h_consistent else 0,
        "h2h_wins_b": int(h2h_b.get("wins", 0)) if h2h_consistent else 0,
        "h2h_last_meeting_at": h2h_a.get("last_meeting_at") if h2h_consistent else None,
        "serve_rate_a": float((independent_a.get("serve") or {}).get("adjusted_rate", 0.62)),
        "serve_rate_b": float((independent_b.get("serve") or {}).get("adjusted_rate", 0.62)),
        "serve_points_a": float((independent_a.get("serve") or {}).get("points", 0.0)),
        "serve_points_b": float((independent_b.get("serve") or {}).get("points", 0.0)),
        "return_rate_a": float((independent_a.get("return") or {}).get("adjusted_rate", 0.38)),
        "return_rate_b": float((independent_b.get("return") or {}).get("adjusted_rate", 0.38)),
        "return_points_a": float((independent_a.get("return") or {}).get("points", 0.0)),
        "return_points_b": float((independent_b.get("return") or {}).get("points", 0.0)),
        "canonical_metrics_digest_a": (left or {}).get("canonical_metrics_digest"),
        "canonical_metrics_digest_b": (right or {}).get("canonical_metrics_digest"),
        "independent_metrics_available_a": bool(independent_a),
        "independent_metrics_available_b": bool(independent_b),
    }
