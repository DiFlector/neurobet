"""Web Research Contracts according to Architecture Section 18."""

from datetime import datetime
from typing import List, Optional
from pydantic import Field
from .base import BaseContract, utc_now


class ResearchEvidence(BaseContract):
    """
    Individual evidence snippet found during web research.
    Strictly qualitative: cannot serve as live score authority.
    """
    evidence_id: str = Field(..., description="Unique evidence identifier")
    url: str = Field(..., description="Raw source URL")
    normalized_url: str = Field(..., description="Normalized canonical URL")
    domain: str = Field(..., description="Source website domain")
    title: str = Field(..., description="Document or article title")
    published_at: Optional[datetime] = Field(default=None, description="Time of publication in UTC")
    retrieved_at: datetime = Field(default_factory=utc_now, description="Fetch timestamp in UTC")
    age_seconds: Optional[int] = Field(default=None, ge=0, description="Age in seconds when retrieved")
    snippet: str = Field(..., description="Extracted relevant text excerpt")
    content_hash: str = Field(..., description="SHA-256 hash of extracted text")
    relevance_score: float = Field(default=1.0, ge=0.0, le=1.0, description="Estimated relevance score")
    freshness_score: float = Field(default=1.0, ge=0.0, le=1.0, description="Freshness coefficient [0.0, 1.0]")
    is_live_score_authority: bool = Field(
        default=False,
        description="Must always be False: research never overrides Fonbet live score",
    )


class ResearchPacket(BaseContract):
    """
    Aggregated contextual research findings for a specific event.
    """
    packet_id: str = Field(..., description="Unique packet identifier")
    event_id: str = Field(..., description="Canonical event ID")
    sport_code: str = Field(default="tennis", description="Sport code")
    query: str = Field(..., description="Search query executed")
    evidence: List[ResearchEvidence] = Field(default_factory=list, description="Found evidence items")
    summary: Optional[str] = Field(default=None, description="Optional brief context summary")
    cached: bool = Field(default=False, description="True if served from research cache")
    created_at: datetime = Field(default_factory=utc_now, description="Creation timestamp in UTC")


class ResearchRequest(BaseContract):
    """
    Input request to research service.
    """
    event_id: str = Field(..., description="Event canonical ID")
    sport_code: str = Field(default="tennis", description="Sport code")
    participant_a: str = Field(..., description="Player or Team A")
    participant_b: str = Field(..., description="Player or Team B")
    tournament: Optional[str] = Field(default=None, description="Tournament or league")
    max_pages: int = Field(default=5, ge=1, le=10, description="Maximum pages to fetch")
    force_refresh: bool = Field(default=False, description="Bypass cache if true")
