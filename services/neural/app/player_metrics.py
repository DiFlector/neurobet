"""Read point-in-time materialized tennis-player metrics."""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select


PLAYER_HISTORY_MAX_AGE = timedelta(days=7)


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def _latest_confirmed_flashscore_id(link_rows) -> Optional[str]:
    """Return the selected source ID from rows already ordered newest first."""
    if not link_rows or link_rows[0].status != "confirmed":
        return None
    return link_rows[0].source_player_id


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
    confirmed_source_id = _latest_confirmed_flashscore_id(link_rows)
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
        if confirmed_source_id:
            # The current profile aggregate may still belong to an identity
            # that was manually replaced. Only immutable snapshots carry the
            # source identity needed to serve a rebind-safe as_of read.
            return None
        eligible_profiles = [row for row in metric_profiles if row.metrics_available_at and row.metrics_available_at <= cutoff]
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
    if available_at is None or _utc(cutoff) - _utc(available_at) > PLAYER_HISTORY_MAX_AGE:
        return None
    return {
        "participant_id": str(participant_id),
        "metrics_version": snapshot.metrics_version if snapshot else metric_owner.metrics_version,
        "available_at": available_at.isoformat(),
        "source_watermark": (snapshot.source_watermark if snapshot else metric_owner.source_watermark).isoformat() if (snapshot.source_watermark if snapshot else metric_owner.source_watermark) else None,
        "history_digest": snapshot.history_digest if snapshot else metric_owner.history_digest,
        "sync_status": profile.sync_status,
        "sync_error": profile.sync_error,
        "profile_url": metric_owner.profile_url,
        "metrics": metrics_as_of(metrics, cutoff),
    }


def pair_features(left: Optional[dict[str, Any]], right: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Build the auditable pair vector from already-materialized player rows."""
    def value(row, *keys, default=0.0):
        current = (row or {}).get("metrics", {})
        for key in keys:
            current = current.get(key, {}) if isinstance(current, dict) else {}
        return current if isinstance(current, (int, float)) else default

    left_metrics = (left or {}).get("metrics", {})
    right_metrics = (right or {}).get("metrics", {})
    elo_a = value(left, "elo", "overall", default=1500.0)
    elo_b = value(right, "elo", "overall", default=1500.0)
    surface_a = value(left, "recent", "days_90", "win_rate", default=0.5)
    surface_b = value(right, "recent", "days_90", "win_rate", default=0.5)
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
    }
