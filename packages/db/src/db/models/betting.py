import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import String, Boolean, Numeric, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ..base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utc_now


class VirtualAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "virtual_accounts"

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="RUB", nullable=False)
    initial_balance: Mapped[float] = mapped_column(Numeric(12, 2), default=100000.00, nullable=False)
    balance: Mapped[float] = mapped_column(Numeric(12, 2), default=100000.00, nullable=False)
    locked_exposure: Mapped[float] = mapped_column(Numeric(12, 2), default=0.00, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    entries: Mapped[List["LedgerEntry"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class LedgerEntry(Base, UUIDPrimaryKeyMixin):
    """
    Immutable financial transaction journal.
    Account balance is always derived and strictly audited via ledger entries.
    """
    __tablename__ = "ledger_entries"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("virtual_accounts.id", ondelete="CASCADE"), nullable=False)
    bet_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)  # INITIAL_DEPOSIT, BET_PLACED, BET_WIN, BET_LOSS, BET_VOID, ADJUSTMENT
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    balance_after: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    account: Mapped["VirtualAccount"] = relationship(back_populates="entries")

    __table_args__ = (
        Index("idx_ledger_entries_account_time", "account_id", "created_at"),
    )


class BetProposal(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "bet_proposals"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    sport_code: Mapped[str] = mapped_column(String(32), default="tennis", nullable=False)
    market: Mapped[str] = mapped_column(String(64), default="match_winner", nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    bookmaker_odds: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    fair_odds: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    model_probability: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    edge: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)
    suggested_stake: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    ml_prediction_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ml_predictions.id", ondelete="SET NULL"), nullable=True)
    llm_decision_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("llm_decisions.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_bet_proposals_event", "event_id", "created_at"),
    )


class BetValidationResult(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "bet_validation_results"

    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bet_proposals.id", ondelete="CASCADE"), nullable=False)
    is_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rejection_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    checks_passed: Mapped[List[str]] = mapped_column(JSONB, default=list, nullable=False)
    checks_failed: Mapped[List[str]] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_bet_validation_proposal", "proposal_id"),
    )


class Bet(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "bets"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("virtual_accounts.id", ondelete="CASCADE"), nullable=False)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bet_proposals.id", ondelete="CASCADE"), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)

    sport_code: Mapped[str] = mapped_column(String(32), default="tennis", nullable=False)
    market: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    odds: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    stake: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    potential_payout: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)  # PENDING, WON, LOST, VOID
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    settlement: Mapped[Optional["BetSettlement"]] = relationship(back_populates="bet", uselist=False)

    __table_args__ = (
        Index("idx_bets_account_status", "account_id", "status"),
        Index("idx_bets_event", "event_id"),
    )


class BetSettlement(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "bet_settlements"

    bet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bets.id", ondelete="CASCADE"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # WON, LOST, VOID
    payout: Mapped[float] = mapped_column(Numeric(12, 2), default=0.00, nullable=False)
    net_profit: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    settlement_reason: Mapped[str] = mapped_column(String(255), default="MATCH_COMPLETED", nullable=False)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    bet: Mapped["Bet"] = relationship(back_populates="settlement")
