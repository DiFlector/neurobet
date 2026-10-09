"""Scheduling and Cost Control Contracts according to Architecture Section 33 and Roadmap Phase 17."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import Field
from .base import BaseContract, utc_now
from .decisions import CandidateItem


class CandidateScoreBreakdown(BaseContract):
    """Normalized multi-factor score breakdown for a betting candidate."""
    edge_score: float = Field(..., ge=0.0, le=1.0, description="Normalized edge factor")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Normalized model confidence")
    liquidity_score: float = Field(..., ge=0.0, le=1.0, description="Estimated market liquidity/rank")
    freshness_score: float = Field(..., ge=0.0, le=1.0, description="Recency of state observation")
    composite_score: float = Field(..., ge=0.0, le=1.0, description="Weighted composite score")


class CandidateSchedulerConfig(BaseContract):
    """Configuration for candidate prioritization, rate limiting, and cooldowns."""
    min_candidate_score: float = Field(
        default=0.60, ge=0.0, le=1.0, description="Minimum composite score to enter priority queue"
    )
    llm_max_rps: float = Field(
        default=2.0, gt=0.0, description="Maximum requests per second dispatched to local LLM"
    )
    browser_max_concurrency: int = Field(
        default=3, ge=1, le=10, description="Maximum concurrent browser fetch sessions for web research"
    )
    research_cooldown_seconds: int = Field(
        default=180, ge=0, description="Cooldown before repeating web research for the same match"
    )
    llm_cooldown_seconds: int = Field(
        default=60, ge=0, description="Cooldown before re-querying LLM for the same match"
    )
    max_queue_size: int = Field(
        default=100, ge=10, le=1000, description="Maximum capacity of priority candidate queue"
    )
    weight_edge: float = Field(default=0.50, ge=0.0, le=1.0, description="Weight for edge in composite score")
    weight_confidence: float = Field(default=0.25, ge=0.0, le=1.0, description="Weight for confidence")
    weight_liquidity: float = Field(default=0.15, ge=0.0, le=1.0, description="Weight for market liquidity")
    weight_freshness: float = Field(default=0.10, ge=0.0, le=1.0, description="Weight for freshness")


class CandidatePriorityItem(BaseContract):
    """Candidate wrapper in the priority queue ordered by composite score."""
    candidate: CandidateItem
    score_breakdown: CandidateScoreBreakdown
    snapshot_version: Optional[str] = Field(default=None, description="Event snapshot version identifier")
    state_hash: Optional[str] = Field(default=None, description="Deterministic state hash for caching")
    enqueued_at: str = Field(default_factory=lambda: utc_now().isoformat(), description="ISO timestamp")


class QueueStatusReport(BaseContract):
    """Real-time observability report on scheduler queue, cooldowns, and caching."""
    queue_size: int = Field(..., description="Current count of queued candidates")
    max_queue_size: int = Field(..., description="Configured capacity of the queue")
    active_research_cooldowns: int = Field(..., description="Count of matches currently in research cooldown")
    active_llm_cooldowns: int = Field(..., description="Count of matches currently in LLM cooldown")
    snapshot_cache_size: int = Field(..., description="Count of cached snapshot state hashes")
    total_evaluated: int = Field(..., description="Total candidates evaluated by scheduler")
    total_dispatched: int = Field(..., description="Total candidates dispatched to pipeline")
    total_cooldown_skips: int = Field(..., description="Candidates where LLM/research was skipped due to cooldown")
    total_cache_hits: int = Field(..., description="Candidates served from snapshot cache")
