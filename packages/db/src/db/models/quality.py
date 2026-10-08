import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from ..base import Base, UUIDPrimaryKeyMixin, utc_now


class DataQualityIssue(Base, UUIDPrimaryKeyMixin):
    """
    Stores detected data quality anomalies and defects.
    Ensures bad records are never silently deleted and remain fully auditable.
    """
    __tablename__ = "data_quality_issues"

    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=True
    )
    source_event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sport_code: Mapped[str] = mapped_column(String(32), default="tennis", nullable=False)
    issue_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="WARNING", nullable=False)  # INFO, WARNING, CRITICAL
    details: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_dq_issues_event", "event_id"),
        Index("idx_dq_issues_type", "issue_type"),
        Index("idx_dq_issues_severity", "severity"),
        Index("idx_dq_issues_detected", "detected_at"),
    )
