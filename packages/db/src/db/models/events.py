import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, Index, BigInteger, Numeric
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ..base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utc_now


class Event(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "events"

    source: Mapped[str] = mapped_column(String(32), default="fonbet", nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sport_code: Mapped[str] = mapped_column(ForeignKey("sports.code", ondelete="CASCADE"), nullable=False)
    league_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("leagues.id", ondelete="SET NULL"), nullable=True)

    participant_a_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("participants.id", ondelete="SET NULL"), nullable=True)
    participant_a_name: Mapped[str] = mapped_column(String(255), nullable=False)
    participant_b_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("participants.id", ondelete="SET NULL"), nullable=True)
    participant_b_name: Mapped[str] = mapped_column(String(255), nullable=False)

    scheduled_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(32), default="prematch", nullable=False)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    current_period: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Set number in tennis
    current_score: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    quality_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), default=100.0, nullable=True)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (
        Index("idx_events_source_unique", "source", "source_event_id", unique=True),
        Index("idx_events_sport_status", "sport_code", "status"),
        Index("idx_events_scheduled_start", "scheduled_start_at"),
    )


class EventStateSnapshot(Base):
    """
    TimescaleDB Hypertable partitioned on observed_at.
    Stores high-resolution chronological evolution of match states.
    """
    __tablename__ = "event_state_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, default=utc_now)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sport_code: Mapped[str] = mapped_column(String(32), default="tennis", nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    match_clock_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_period: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    score: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    score_detail: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    sport_state: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)  # Server, points, aces
    raw_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_event_state_snapshots_lookup", "event_id", "observed_at"),
        Index("idx_event_state_snapshots_sport", "sport_code", "observed_at"),
    )


class RawSnapshot(Base, UUIDPrimaryKeyMixin):
    """
    Metadata registry for raw HTML/JSON payloads stored in S3/MinIO.
    """
    __tablename__ = "raw_snapshots"

    source: Mapped[str] = mapped_column(String(32), default="fonbet", nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    sport_code: Mapped[str] = mapped_column(String(32), nullable=False)
    page_type: Mapped[str] = mapped_column(String(32), nullable=False)  # live, prematch, results
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    collector_version: Mapped[str] = mapped_column(String(32), default="1.0.0", nullable=False)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (
        Index("idx_raw_snapshots_hash", "content_hash"),
        Index("idx_raw_snapshots_time", "collected_at"),
    )
