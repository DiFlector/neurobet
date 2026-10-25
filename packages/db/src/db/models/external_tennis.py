"""External tennis history. Results become visible only at available_at."""

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, utc_now


class ExtTennisPlayer(Base):
    __tablename__ = "ext_tennis_players"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_player_id: Mapped[str] = mapped_column(String(64), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    gender: Mapped[str] = mapped_column(String(8), default="unknown", nullable=False)
    hand: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    ioc: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("source", "source_player_id", name="uq_ext_tennis_players_source"),
        Index("idx_ext_tennis_players_name", "full_name"),
    )


class ExtTennisMatch(Base):
    __tablename__ = "ext_tennis_matches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_match_id: Mapped[str] = mapped_column(String(128), nullable=False)
    played_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tour_level: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    tournament: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    surface: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    best_of: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    round_name: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    gender: Mapped[str] = mapped_column(String(8), default="unknown", nullable=False)
    winner_name: Mapped[str] = mapped_column(String(255), nullable=False)
    loser_name: Mapped[str] = mapped_column(String(255), nullable=False)
    winner_player_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    loser_player_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    winner_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    loser_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    score: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    odds_winner: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    odds_loser: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    serve_won_winner: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    serve_won_loser: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    return_won_winner: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    return_won_loser: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stats: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("source", "source_match_id", name="uq_ext_tennis_matches_source"),
        Index("idx_ext_tennis_matches_source_played", "source", "played_at"),
        Index("idx_ext_tennis_matches_available", "available_at"),
        Index("idx_ext_tennis_matches_players", "winner_name", "loser_name"),
        Index("idx_ext_tennis_matches_source_winner_id", "source", "winner_player_id"),
        Index("idx_ext_tennis_matches_source_loser_id", "source", "loser_player_id"),
    )


class ExtTennisMatchObservation(Base):
    """Immutable source-specific version of a parsed match result."""

    __tablename__ = "ext_tennis_match_observations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_match_id: Mapped[str] = mapped_column(String(128), nullable=False)
    observation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    source_content_hash: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("source", "source_match_id", "available_at", name="uq_ext_tennis_match_observation_version"),
        Index("idx_ext_tennis_match_observation_time", "source", "source_match_id", "available_at"),
        Index(
            "idx_api_tennis_observation_available_cursor", "available_at", "source_match_id",
            postgresql_where=text("source = 'api_tennis'"),
        ),
        Index(
            "idx_mcp_observation_payload_date", text("(payload->>'date')"), "source_match_id",
            postgresql_where=text("source = 'match_charting_project'"),
        ),
    )


class TennisPlayerProfile(Base):
    """Current identity/sync state and denormalized metrics for one Fonbet participant."""

    __tablename__ = "tennis_player_profiles"

    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True
    )
    canonical_player_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ext_tennis_canonical_players.id", ondelete="SET NULL"), nullable=True
    )
    source_identities: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    profile_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    sync_error: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    source_watermark: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_full_history_scan_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    history_match_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metrics_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metrics_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    history_digest: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metrics_available_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_tennis_player_profiles_canonical", "canonical_player_id"),
        Index("idx_tennis_player_profiles_sync", "sync_status", "last_attempt_at"),
    )


class TennisPlayerIdentityLink(Base):
    """Append-only, point-in-time evidence linking a Fonbet participant to a source player."""
    __tablename__ = "tennis_player_identity_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("participants.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_player_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_tennis_player_identity_asof", "participant_id", "source", "available_at"),
        Index("idx_tennis_player_identity_source", "source", "source_player_id", "available_at"),
    )


class TennisPlayerRefreshJob(Base):
    """Durable, coalesced refresh work item keyed by canonical participant."""

    __tablename__ = "tennis_player_refresh_jobs"

    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(24), default="idle", nullable=False)
    requested_reason: Mapped[str] = mapped_column(String(48), default="event", nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_token: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    progress_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_tennis_player_refresh_dispatch", "status", "next_run_at", "lease_until"),
    )


class TennisPlayerMetricSnapshot(Base):
    """Immutable metric versions used by historical training and replay."""

    __tablename__ = "tennis_player_metric_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("participants.id", ondelete="CASCADE"), nullable=False
    )
    source_player_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metrics_version: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_watermark: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    history_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("participant_id", "metrics_version", "history_digest", name="uq_tennis_player_metric_snapshot"),
        Index("idx_tennis_player_metric_asof", "participant_id", "available_at"),
    )


class TennisPlayerDataCoverage(Base):
    """Last verified source coverage for one player's independently usable data capability."""

    __tablename__ = "tennis_player_data_coverage"

    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    capability: Mapped[str] = mapped_column(String(32), primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_watermark: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (
        Index("idx_tennis_player_data_coverage_fresh", "participant_id", "capability", "checked_at"),
    )


class TennisPlayerResultFact(Base):
    """Idempotent per-player contribution; corrections replace the fact and dirty the profile."""

    __tablename__ = "tennis_player_result_facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("participants.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_match_id: Mapped[str] = mapped_column(String(128), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    played_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    surface: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    tour_level: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    won: Mapped[bool] = mapped_column(Boolean, nullable=False)
    valid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sets_played: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    serve_points: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    serve_points_won: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("participant_id", "source", "source_match_id", "available_at", name="uq_tennis_player_result_fact_version"),
        Index("idx_tennis_player_fact_time", "participant_id", "played_at", "available_at"),
    )


class TennisPointDocument(Base):
    """Archived point-by-point source document as observed at a specific time."""
    __tablename__ = "tennis_point_documents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="flashscore")
    source_match_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    archived_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parsed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    availability_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown_legacy")
    retrospective: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    parse_status: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("idx_tennis_point_document_asof", "source_match_id", "source_url", "retrieved_at"),)


class TennisPointHistoryVersion(Base):
    """Immutable parser output; each changed document creates a new version."""
    __tablename__ = "tennis_point_history_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tennis_point_documents.id", ondelete="CASCADE"), nullable=False)
    source_match_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fonbet_event_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id", ondelete="SET NULL"), nullable=True)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parsed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    availability_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown_legacy")
    events_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    quality_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    retrospective: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (Index("idx_tennis_point_history_event_available", "fonbet_event_id", "available_at"),)


class FlashscoreImportJob(Base):
    """Durable, coalesced full-profile import requested by the worker/API."""
    __tablename__ = "flashscore_import_jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    profile_urls: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    progress_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_token: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    last_error: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (Index("idx_flashscore_import_dispatch", "status", "next_run_at", "lease_until"),)


class TennisPointMatchLink(Base):
    """Candidate or manually resolved Flashscore to Fonbet event association."""
    __tablename__ = "tennis_point_match_links"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_match_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fonbet_event_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="unmatched")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TennisPointRefreshJob(Base):
    """Coalesced durable polling job for one live Fonbet event."""
    __tablename__ = "tennis_point_refresh_jobs"
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_token: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    last_requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    last_error: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (Index("idx_tennis_point_refresh_dispatch", "status", "next_run_at", "lease_until"),)


class TennisPointRefreshJobEvent(Base):
    """Append-only status history used by point-history as-of reads."""
    __tablename__ = "tennis_point_refresh_job_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    source_match_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("idx_tennis_point_job_event_asof", "event_id", "available_at"),)


class TennisPointSourceRefreshJob(Base):
    """Durable PBP retrieval keyed by source identity, independent of Fonbet links."""
    __tablename__ = "tennis_point_source_refresh_jobs"
    source_match_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_token: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    last_error: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (Index("idx_tennis_point_source_refresh_dispatch", "status", "next_run_at", "lease_until"),)


class TennisMatchLookupJob(Base):
    """Durable lookup for the external live match associated with a Fonbet event."""
    __tablename__ = "tennis_match_lookup_jobs"
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_token: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    source_match_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (Index("idx_tennis_match_lookup_dispatch", "status", "next_run_at", "lease_until"),)


class ManualConfirmationRequest(Base):
    """Durable review item for an uncertain external identity or match link."""
    __tablename__ = "manual_confirmation_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="flashscore")
    evidence_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("idx_manual_confirmation_status_created", "status", "created_at"),
        Index("idx_manual_confirmation_subject", "kind", "subject_id", "status"),
        Index("uq_manual_confirmation_pending_pair", "kind", "subject_id", "candidate_id", "source",
              unique=True, postgresql_where=text("status = 'pending'")),
    )


class ManualConfirmationDecision(Base):
    """Append-only audit trail for every human confirmation decision."""
    __tablename__ = "manual_confirmation_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manual_confirmation_requests.id", ondelete="CASCADE"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    request_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decided_by: Mapped[str] = mapped_column(String(128), nullable=False)
    details_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (Index("idx_manual_confirmation_decisions_request", "request_id", "created_at"),)


class ExtTennisRanking(Base):
    """Official ranking known on ranking_date. Later lists are not visible earlier."""

    __tablename__ = "ext_tennis_rankings"

    tour: Mapped[str] = mapped_column(String(8), primary_key=True)
    ranking_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    source_player_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class ExtTennisCanonicalPlayer(Base):
    """One player after Sackmann ids and surname/initial have been merged."""

    __tablename__ = "ext_tennis_canonical_players"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    gender: Mapped[str] = mapped_column(String(8), default="unknown", nullable=False)
    hand: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    ioc: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    surname_key: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    initial: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    source_ids: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_ext_tennis_canonical_players_name", "surname_key", "initial", "gender"),
        Index("idx_ext_tennis_canonical_players_api_id", text("(source_ids->>'api_tennis')")),
    )


class ExtTennisCanonicalPlayerAlias(Base):
    """Append-only provider ID aliases; one canonical player may have many IDs."""

    __tablename__ = "ext_tennis_canonical_player_aliases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_player_id: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_player_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ext_tennis_canonical_players.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="confirmed")
    evidence_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('confirmed', 'candidate', 'rejected')", name="ck_ext_tennis_player_alias_status"),
        UniqueConstraint(
            "source", "source_player_id", "canonical_player_id",
            name="uq_ext_tennis_canonical_player_alias_candidate",
        ),
        Index("idx_ext_tennis_player_alias_lookup", "source", "source_player_id", "status"),
        Index("idx_ext_tennis_player_alias_person", "canonical_player_id", "source", "status"),
        Index(
            "uq_ext_tennis_player_alias_confirmed", "source", "source_player_id", unique=True,
            postgresql_where=text("status = 'confirmed'"),
        ),
    )


class ExtTennisCanonicalPlayerAliasDecision(Base):
    """Append-only audit record for a human-reviewed alias conflict decision."""

    __tablename__ = "ext_tennis_canonical_player_alias_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_player_id: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_player_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ext_tennis_canonical_players.id", ondelete="RESTRICT"), nullable=False
    )
    rejected_candidate_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    rationale: Mapped[str] = mapped_column(String(2000), nullable=False)
    evidence_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    decided_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_ext_tennis_alias_decision_lookup", "source", "source_player_id", "created_at"),
    )


class ExtTennisCanonicalMatch(Base):
    """One row per match. Raw source rows stay in ext_tennis_matches."""

    __tablename__ = "ext_tennis_canonical_matches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(64), nullable=False)
    played_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tour_level: Mapped[str] = mapped_column(String(32), default="other", nullable=False)
    tournament: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    surface: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    best_of: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    round_name: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    gender: Mapped[str] = mapped_column(String(8), default="unknown", nullable=False)
    winner_name: Mapped[str] = mapped_column(String(255), nullable=False)
    loser_name: Mapped[str] = mapped_column(String(255), nullable=False)
    winner_player_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    loser_player_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    winner_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    loser_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    score: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    odds_winner: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    odds_loser: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    serve_won_winner: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    serve_won_loser: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    return_won_winner: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    return_won_loser: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    odds_source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    stats_source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    stats: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    duplicate_of_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ext_tennis_canonical_matches.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        CheckConstraint("duplicate_of_id IS NULL OR duplicate_of_id <> id", name="ck_ext_tennis_match_not_self_duplicate"),
        UniqueConstraint("canonical_key", name="uq_ext_tennis_canonical_key"),
        Index("idx_ext_tennis_canonical_available", "available_at"),
        Index("idx_ext_tennis_canonical_played_gender", "played_at", "gender"),
        Index("idx_ext_tennis_canonical_duplicate_of", "duplicate_of_id", postgresql_where=text("duplicate_of_id IS NOT NULL")),
        Index("idx_ext_tennis_canonical_players", "winner_player_id", "loser_player_id"),
        Index("idx_ext_tennis_canonical_loser", "loser_player_id", "played_at", "canonical_key"),
    )


class ExtTennisServeLog(Base):
    """Charted serve points with separate event and first-known timestamps."""

    __tablename__ = "ext_tennis_serve_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_match_id: Mapped[str] = mapped_column(String(160), nullable=False)
    player_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_event_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    legacy_available_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    availability_provenance: Mapped[str] = mapped_column(String(64), default="database_ingest_time", nullable=False)
    source_confidence: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    retrospective: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    surface: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    serve_points: Mapped[float] = mapped_column(Float, nullable=False)
    serve_points_won: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("source", "source_match_id", "player_name", name="uq_ext_tennis_serve_log"),
        Index("idx_ext_tennis_serve_player", "player_name", "available_at"),
    )
