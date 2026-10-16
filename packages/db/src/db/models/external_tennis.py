"""External tennis history. Results become visible only at available_at."""

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, Float, Index, Integer, String, UniqueConstraint
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
        Index("idx_ext_tennis_matches_available", "available_at"),
        Index("idx_ext_tennis_matches_players", "winner_name", "loser_name"),
    )


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("canonical_key", name="uq_ext_tennis_canonical_key"),
        Index("idx_ext_tennis_canonical_available", "available_at"),
        Index("idx_ext_tennis_canonical_players", "winner_player_id", "loser_player_id"),
    )


class ExtTennisServeLog(Base):
    """Charted serve points. A row is usable only at available_at."""

    __tablename__ = "ext_tennis_serve_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_match_id: Mapped[str] = mapped_column(String(160), nullable=False)
    player_name: Mapped[str] = mapped_column(String(255), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    surface: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    serve_points: Mapped[float] = mapped_column(Float, nullable=False)
    serve_points_won: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("source", "source_match_id", "player_name", name="uq_ext_tennis_serve_log"),
        Index("idx_ext_tennis_serve_player", "player_name", "available_at"),
    )
