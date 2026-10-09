import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, Numeric, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from ..base import Base, UUIDPrimaryKeyMixin, utc_now


class WebResearchRun(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "web_research_runs"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="completed", nullable=False)
    documents_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_research_runs_event", "event_id", "created_at"),
    )


class WebDocument(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "web_documents"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("web_research_runs.id", ondelete="CASCADE"), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_domain: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_storage_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        Index("idx_web_documents_hash", "content_hash"),
    )


class WebEvidence(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "web_evidence"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("web_documents.id", ondelete="CASCADE"), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    snippet: Mapped[str] = mapped_column(String(2048), nullable=False)
    relevance_score: Mapped[float] = mapped_column(Numeric(6, 4), default=1.0, nullable=False)
    freshness_score: Mapped[float] = mapped_column(Numeric(6, 4), default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_web_evidence_event", "event_id", "created_at"),
    )
