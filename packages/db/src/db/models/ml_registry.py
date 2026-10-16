import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import String, Integer, Numeric, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from ..base import Base, UUIDPrimaryKeyMixin, utc_now


class ModelVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "model_versions"

    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    version_tag: Mapped[str] = mapped_column(String(64), nullable=False)
    sport_code: Mapped[str] = mapped_column(String(32), default="tennis", nullable=False)
    market: Mapped[str] = mapped_column(String(64), default="match_winner", nullable=False)
    artifact_path: Mapped[str] = mapped_column(String(512), nullable=False)
    metrics: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    hyperparameters: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)  # ACTIVE, SHADOW, RETIRED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "sport_code",
            "model_name",
            "market",
            "version_tag",
            name="uq_model_versions_line",
        ),
    )


class TrainingDataset(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "training_datasets"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    sport_code: Mapped[str] = mapped_column(String(32), default="tennis", nullable=False)
    date_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class TrainingRun(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "training_runs"

    model_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True)
    dataset_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("training_datasets.id", ondelete="SET NULL"), nullable=True)
    sport_code: Mapped[str] = mapped_column(String(32), default="tennis", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="completed", nullable=False)
    metrics_train: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    metrics_val: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ExperimentResult(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "experiment_results"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True)
    strategy_config: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    backtest_pnl: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    backtest_roi: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)
    win_rate: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    max_drawdown: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class BacktestRun(Base, UUIDPrimaryKeyMixin):
    """Historical replay job. It does not create production bets or ledger rows."""

    __tablename__ = "backtest_runs"

    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    sport_code: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    model_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    request_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    error: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_backtest_runs_status_created", "status", "created_at"),
        Index("idx_backtest_runs_sport_created", "sport_code", "created_at"),
    )


class BacktestRunResult(Base, UUIDPrimaryKeyMixin):
    """Paper result of one model inside a historical replay."""

    __tablename__ = "backtest_run_results"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False)
    model_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    summary: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    final_balance: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    roi: Mapped[Optional[float]] = mapped_column(Numeric(10, 4), nullable=True)
    bankruptcies: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_backtest_run_results_run", "run_id"),
    )


class AuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_log"

    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    details: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_audit_log_action_time", "action", "created_at"),
    )
