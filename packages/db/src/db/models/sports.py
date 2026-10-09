import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import String, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ..base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utc_now


class Sport(Base, TimestampMixin):
    __tablename__ = "sports"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    leagues: Mapped[List["League"]] = relationship(back_populates="sport", cascade="all, delete-orphan")
    participants: Mapped[List["Participant"]] = relationship(back_populates="sport", cascade="all, delete-orphan")


class League(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "leagues"

    sport_code: Mapped[str] = mapped_column(ForeignKey("sports.code", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="fonbet", nullable=False)
    source_league_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    surface: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # For tennis: hard, clay, grass
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    sport: Mapped["Sport"] = relationship(back_populates="leagues")

    __table_args__ = (
        Index("idx_leagues_source", "source", "source_league_id"),
        Index("idx_leagues_sport", "sport_code"),
    )


class Participant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "participants"

    sport_code: Mapped[str] = mapped_column(ForeignKey("sports.code", ondelete="CASCADE"), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_team: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # False for tennis players
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    sport: Mapped["Sport"] = relationship(back_populates="participants")
    aliases: Mapped[List["ParticipantAlias"]] = relationship(back_populates="participant", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_participants_sport_name", "sport_code", "canonical_name"),
    )


class ParticipantAlias(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "participant_aliases"

    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="fonbet", nullable=False)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    participant: Mapped["Participant"] = relationship(back_populates="aliases")

    __table_args__ = (
        Index("idx_participant_aliases_lookup", "source", "alias"),
    )
