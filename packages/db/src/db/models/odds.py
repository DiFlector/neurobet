import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import String, Boolean, Numeric, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ..base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utc_now


class Market(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "markets"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    source_market_id: Mapped[str] = mapped_column(String(64), nullable=False)
    market_type: Mapped[str] = mapped_column(String(64), default="match_winner", nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    line_value: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True)

    selections: Mapped[List["MarketSelection"]] = relationship(back_populates="market", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_markets_event_type", "event_id", "market_type"),
    )


class MarketSelection(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "market_selections"

    market_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), nullable=False)
    source_selection_id: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)  # player_a, player_b
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)

    market: Mapped["Market"] = relationship(back_populates="selections")

    __table_args__ = (
        Index("idx_selections_market_outcome", "market_id", "outcome"),
    )


class OddsSnapshot(Base):
    """
    TimescaleDB Hypertable partitioned on observed_at.
    Stores complete historical time-series of odds.
    """
    __tablename__ = "odds_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, default=utc_now)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    market_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    selection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    market_type: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    line_value: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True)
    odds: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    probability_implied: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)

    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_odds_snapshots_event_time", "event_id", "observed_at"),
        Index("idx_odds_snapshots_market_lookup", "event_id", "market_type", "observed_at"),
        Index("idx_odds_snapshots_selection", "selection_id", "observed_at"),
    )


class OddsChangeEvent(Base, UUIDPrimaryKeyMixin):
    """
    Derived event emitted when odds change for an outcome.
    """
    __tablename__ = "odds_change_events"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    selection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market_selections.id", ondelete="CASCADE"), nullable=False)
    old_odds: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    new_odds: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    delta: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_odds_changes_event_time", "event_id", "changed_at"),
    )
