from datetime import datetime
from typing import List, Optional
from pydantic import Field
from .base import BaseContract, utc_now


class ResearchEvidence(BaseContract):
    """
    Individual evidence snippet found during web research.
    """
    evidence_id: str = Field(..., description="Unique evidence identifier")
    url: str = Field(..., description="Source URL")
    source_name: str = Field(..., description="Domain or name of news/stats outlet")
    published_at: Optional[datetime] = Field(default=None, description="Time of publication in UTC")
    fetched_at: datetime = Field(default_factory=utc_now, description="Fetch timestamp in UTC")
    snippet: str = Field(..., description="Extracted relevant text excerpt")
    relevance_score: float = Field(default=1.0, ge=0.0, le=1.0, description="Estimated relevance to the match")
    freshness_score: float = Field(default=1.0, ge=0.0, le=1.0, description="Freshness coefficient")


class ResearchPacket(BaseContract):
    """
    Aggregated contextual research findings for a specific event.
    """
    packet_id: str = Field(..., description="Unique packet identifier")
    event_id: str = Field(..., description="Canonical event ID")
    sport_code: str = Field(default="tennis", description="Sport code")
    evidence: List[ResearchEvidence] = Field(default_factory=list, description="Found evidence items")
    summary: Optional[str] = Field(default=None, description="Optional brief context summary")
