"""Point-in-time freshness checks for independently available tennis data."""

from datetime import datetime, timedelta, timezone
import os

from sqlalchemy import select


PLAYER_DATA_MAX_AGE = timedelta(days=7)
CAPABILITIES = {"match_history", "serve_statistics", "point_by_point"}
CAPABILITY_SOURCES = {
    "match_history": ("tennis_my_life", "tennis_explorer", "tennis_abstract", "api_tennis", "flashscore"),
    "serve_statistics": ("tennis_my_life", "api_tennis", "flashscore"),
    "point_by_point": ("api_tennis", "match_charting_project", "flashscore"),
}
SOURCE_ALIASES = {
    "tml": "tennis_my_life",
    "mcp": "match_charting_project",
}


def canonical_source_name(source: str) -> str:
    """Map provider-internal aliases to the source ID used by routing and API."""
    return SOURCE_ALIASES.get(str(source).strip().casefold(), str(source).strip().casefold())


def source_capability_enabled(source: str, capability: str) -> bool:
    """Read the per-source rollout gate; unspecified gates preserve current behavior."""
    source = canonical_source_name(source)
    if capability not in CAPABILITIES or source not in CAPABILITY_SOURCES[capability]:
        return False
    source_key = source.upper().replace("-", "_")
    capability_key = capability.upper()
    value = os.getenv(f"TENNIS_SOURCE_{source_key}_{capability_key}_ENABLED", "true")
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def enabled_sources_for_capability(capability: str) -> tuple[str, ...]:
    if capability not in CAPABILITIES:
        raise ValueError(f"unsupported tennis data capability: {capability}")
    return tuple(source for source in CAPABILITY_SOURCES[capability]
                 if source_capability_enabled(source, capability))


def cache_eligible_history_sources() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return enabled sources for complete and approved partial history cache hits."""
    enabled = enabled_sources_for_capability("match_history")
    partial = tuple(source for source in enabled
                    if source in {"api_tennis", "tennis_explorer", "tennis_abstract"})
    return enabled, partial


def coverage_is_fresh(checked_at: datetime | None, *, as_of: datetime,
                      status: str, complete: bool) -> bool:
    """A cache is current only after a successful full fetch at or before the cutoff."""
    if checked_at is None or status != "available" or not complete:
        return False
    cutoff = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)
    checked = checked_at.replace(tzinfo=timezone.utc) if checked_at.tzinfo is None else checked_at.astimezone(timezone.utc)
    return timedelta(0) <= cutoff - checked <= PLAYER_DATA_MAX_AGE


def partial_history_is_fresh(checked_at: datetime | None, *, as_of: datetime,
                             status: str, complete: bool, source: str) -> bool:
    """Partial history is usable only for explicitly projected source snapshots."""
    if source not in {"api_tennis", "tennis_explorer", "tennis_abstract"} or status != "partial" or complete:
        return False
    if checked_at is None:
        return False
    cutoff = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)
    checked = checked_at.replace(tzinfo=timezone.utc) if checked_at.tzinfo is None else checked_at.astimezone(timezone.utc)
    return timedelta(0) <= cutoff - checked <= PLAYER_DATA_MAX_AGE


def partial_coverage_is_usable(coverage, *, as_of: datetime) -> bool:
    """Fail closed unless a fresh partial source snapshot was approved for metrics."""
    if coverage is None:
        return False
    return (
        partial_history_is_fresh(
            coverage.checked_at, as_of=as_of, status=coverage.status,
            complete=coverage.complete, source=coverage.source,
        )
        and (coverage.evidence_json or {}).get("usable_for_metrics") is True
    )


def profile_needs_history_refresh(*, metrics_version: int, metrics_available_at: datetime | None,
                                 as_of: datetime, complete_coverage, partial_coverage,
                                 flashscore_last_success_at: datetime | None,
                                 identity_confirmation_required: bool, attempt_recent: bool) -> bool:
    """Decide whether profile history needs scraping, independent of other capabilities."""
    cutoff = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)
    def utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    metrics_fresh = bool(
        metrics_available_at is not None
        # Metrics can be materialized during this request, just after its
        # initial as-of timestamp. The underlying source coverage still has a
        # strict cutoff; tolerate only this tiny local-write skew.
        and utc(metrics_available_at) <= cutoff + timedelta(seconds=5)
        and utc(metrics_available_at) >= cutoff - PLAYER_DATA_MAX_AGE
    )
    flashscore_fresh = bool(
        source_capability_enabled("flashscore", "match_history")
        and flashscore_last_success_at is not None
        and utc(flashscore_last_success_at) <= cutoff
        and utc(flashscore_last_success_at) >= cutoff - PLAYER_DATA_MAX_AGE
    )
    history_usable = bool(metrics_fresh and (complete_coverage or partial_coverage or flashscore_fresh))
    return bool(
        not attempt_recent and not identity_confirmation_required
        and (metrics_version <= 0 or not history_usable)
    )


def fresh_coverage(session, participant_id, *, as_of: datetime, capability: str = "match_history"):
    """Return a complete, recently checked source row without crossing the as-of time."""
    from db.models.external_tennis import TennisPlayerDataCoverage

    if capability not in CAPABILITIES:
        raise ValueError(f"unsupported tennis data capability: {capability}")
    enabled_sources = enabled_sources_for_capability(capability)
    if not enabled_sources:
        return None
    cutoff = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)
    return session.execute(
        select(TennisPlayerDataCoverage).where(
            TennisPlayerDataCoverage.participant_id == participant_id,
            TennisPlayerDataCoverage.capability == capability,
            TennisPlayerDataCoverage.source.in_(enabled_sources),
            TennisPlayerDataCoverage.status == "available",
            TennisPlayerDataCoverage.complete.is_(True),
            TennisPlayerDataCoverage.checked_at <= cutoff,
            TennisPlayerDataCoverage.checked_at >= cutoff - PLAYER_DATA_MAX_AGE,
        ).order_by(TennisPlayerDataCoverage.checked_at.desc())
    ).scalars().first()


def fresh_partial_history_coverage(session, participant_id, *, as_of: datetime):
    """Return a recent, explicitly usable partial history without claiming completeness.

    Partial snapshots are valid inputs to player metrics, but callers must keep
    their coverage state visible and must not treat them as a complete archive.
    This is intentionally limited to match history; partial statistics/PBP do
    not satisfy those independent capabilities.
    """
    from db.models.external_tennis import TennisPlayerDataCoverage

    enabled_sources = tuple(source for source in enabled_sources_for_capability("match_history")
                            if source in {"api_tennis", "tennis_explorer", "tennis_abstract"})
    if not enabled_sources:
        return None
    cutoff = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)
    rows = session.execute(
        select(TennisPlayerDataCoverage).where(
            TennisPlayerDataCoverage.participant_id == participant_id,
            TennisPlayerDataCoverage.capability == "match_history",
            TennisPlayerDataCoverage.status == "partial",
            TennisPlayerDataCoverage.complete.is_(False),
            TennisPlayerDataCoverage.source.in_(enabled_sources),
            TennisPlayerDataCoverage.checked_at <= cutoff,
            TennisPlayerDataCoverage.checked_at >= cutoff - PLAYER_DATA_MAX_AGE,
        ).order_by(TennisPlayerDataCoverage.checked_at.desc())
    ).scalars().all()
    # A newer partial snapshot may be unusable (for example, a provider
    # returned only a shell page). Keep looking so it cannot shadow an older
    # but explicitly approved snapshot that is still inside the freshness
    # window and already contains usable metrics.
    return next((row for row in rows if partial_coverage_is_usable(row, as_of=cutoff)), None)


def upsert_coverage(session, *, participant_id, source: str, capability: str,
                    checked_at: datetime, source_watermark: datetime | None,
                    status: str, complete: bool, evidence: dict | None = None) -> None:
    """Idempotently publish the latest source coverage for a player capability."""
    from db.models.external_tennis import TennisPlayerDataCoverage

    if capability not in CAPABILITIES:
        raise ValueError(f"unsupported tennis data capability: {capability}")
    source = canonical_source_name(source)
    row = session.get(TennisPlayerDataCoverage, (participant_id, source, capability))
    values = dict(checked_at=checked_at, source_watermark=source_watermark,
                  status=status, complete=bool(complete), evidence_json=evidence or {})
    if row is None:
        session.add(TennisPlayerDataCoverage(participant_id=participant_id, source=source,
                                             capability=capability, **values))
    elif row.checked_at <= checked_at:
        for key, value in values.items():
            setattr(row, key, value)
