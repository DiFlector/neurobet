"""LLM Input and Output Contracts according to Architecture Sections 19 & 20."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import Field
from .base import BaseContract


class LLMEventContext(BaseContract):
    """Event state context provided to LLM."""
    sport: str = Field(default="tennis", description="Sport code")
    league: Optional[str] = Field(default=None, description="Tournament / league name")
    home: str = Field(..., description="Participant A or Home team")
    away: str = Field(..., description="Participant B or Away team")
    scheduled_start: Optional[str] = Field(default=None, description="Scheduled start ISO timestamp")
    status: str = Field(default="live", description="Event status: live or scheduled")
    clock: Optional[str] = Field(default=None, description="Current match time or set score")
    score: Dict[str, Any] = Field(default_factory=dict, description="Current match score structure")


class LLMMarketContext(BaseContract):
    """Market and selection context provided to LLM."""
    type: str = Field(default="match_winner", description="Market type")
    line: Optional[float] = Field(default=None, description="Handicap or total line if applicable")
    selection: str = Field(..., description="Proposed selection ID, e.g. player_a")
    odds: float = Field(..., gt=1.0, description="Offered bookmaker decimal odds")
    observed_at: Optional[str] = Field(default=None, description="Observation timestamp")


class LLMMLContext(BaseContract):
    """Statistical ML model outputs provided to LLM."""
    probability: float = Field(..., ge=0.0, le=1.0, description="Model calibrated probability")
    market_probability: float = Field(..., ge=0.0, le=1.0, description="Implied probability from odds")
    edge: float = Field(..., description="Calculated edge (probability - market_probability)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model confidence score")
    model_version: str = Field(..., description="Version of statistical ML model")


class LLMResearchItem(BaseContract):
    """External qualitative research finding provided to LLM."""
    title: str = Field(..., description="Article or note headline")
    domain: str = Field(..., description="Source website domain")
    published_at: Optional[str] = Field(default=None, description="Publication timestamp")
    snippet: str = Field(..., description="Extracted contextual excerpt")


class LLMConstraints(BaseContract):
    """Operational constraints provided to LLM."""
    max_stake_fraction: float = Field(default=0.01, le=0.05, description="Max stake fraction of bankroll")
    max_bet_age_seconds: int = Field(default=15, description="Maximum age of betting proposal")
    simulation_only: bool = Field(default=True, description="Strict simulation mode flag")


class LLMAnalysisInput(BaseContract):
    """
    Architecture Section 19: LLM Input Contract.
    Compact, structured JSON passed to the local LLM.
    """
    event: LLMEventContext
    market: LLMMarketContext
    ml: LLMMLContext
    research: List[LLMResearchItem] = Field(default_factory=list)
    constraints: LLMConstraints = Field(default_factory=LLMConstraints)


class LLMEvidenceItem(BaseContract):
    """Source reference supporting LLM verdict."""
    url: Optional[str] = Field(default=None, description="Source URL if available")
    domain: str = Field(..., description="Source domain")
    note: Optional[str] = Field(default=None, description="Short factual note")


class LLMStructuredVerdict(BaseContract):
    """
    Architecture Section 20: LLM Output Contract.
    Strictly enforced Pydantic schema for local LLM output.
    Any non-conforming or corrupted output falls back to INSUFFICIENT_DATA.
    """
    verdict: Literal["BET", "NO_BET", "INSUFFICIENT_DATA"] = Field(
        ...,
        description="Overall LLM qualitative verdict",
    )
    market_type: str = Field(..., description="Target market type, e.g. match_winner")
    selection_id: str = Field(..., description="Confirmed selection ID, e.g. player_a")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Qualitative confidence score bounded in [0.0, 1.0]",
    )
    stake_recommendation_fraction: float = Field(
        default=0.0,
        ge=0.0,
        le=0.05,
        description="Recommended fraction of bankroll (capped at 5%)",
    )
    reason_codes: List[str] = Field(
        default_factory=list,
        description="Standardized analytical codes, e.g. MODEL_EDGE, INJURY_RISK, SCHEDULE_FATIGUE",
    )
    contradictions: List[str] = Field(
        default_factory=list,
        description="Contradicting signals identified between ML and external context",
    )
    research_quality: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Assessment of external context reliability",
    )
    research_freshness_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description="Age of newest external research in seconds",
    )
    invalid_or_missing_data: List[str] = Field(
        default_factory=list,
        description="Missing fields or data validation anomalies",
    )
    summary: str = Field(
        ...,
        max_length=1500,
        description="Factual, concise analytical synthesis",
    )
    evidence: List[LLMEvidenceItem] = Field(
        default_factory=list,
        description="List of factual references",
    )
    latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Inference duration in milliseconds",
    )
    model_identifier: str = Field(
        default="unknown",
        description="Exact model name/path used for inference",
    )
    prompt_version: str = Field(
        default="neurobet_llm_v1.0",
        description="Version of prompt template",
    )

    @classmethod
    def create_insufficient_data_fallback(
        cls,
        market_type: str = "unknown",
        selection_id: str = "unknown",
        reason: str = "INVALID_OR_CORRUPTED_LLM_OUTPUT",
        model_identifier: str = "unknown",
        prompt_version: str = "neurobet_llm_v1.0",
        latency_ms: float = 0.0,
    ) -> "LLMStructuredVerdict":
        """Fallback factory when LLM produces invalid JSON or schema errors."""
        return cls(
            verdict="INSUFFICIENT_DATA",
            market_type=market_type,
            selection_id=selection_id,
            confidence=0.0,
            stake_recommendation_fraction=0.0,
            reason_codes=[reason],
            contradictions=[],
            research_quality=0.0,
            research_freshness_seconds=None,
            invalid_or_missing_data=[reason],
            summary="LLM analysis produced invalid or insufficient data. Bet aborted.",
            evidence=[],
            latency_ms=latency_ms,
            model_identifier=model_identifier,
            prompt_version=prompt_version,
        )


class LLMDecision(BaseContract):
    """
    Structured analytical output from local LLM for persistence and pipeline compatibility.
    LLM NEVER places bets directly, only provides contextual risk/support verdicts.
    """
    decision_id: str = Field(..., description="Unique decision identifier")
    event_id: str = Field(..., description="Canonical event ID")
    llm_version: str = Field(..., description="LLM model tag and prompt template version")
    context_quality: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence in completeness of external context",
    )
    injury_risk: Literal["low", "medium", "high", "unknown"] = Field(
        default="unknown",
        description="Assessed risk of injury/retirement",
    )
    fatigue_risk: Literal["low", "medium", "high", "unknown"] = Field(
        default="unknown",
        description="Assessed fatigue level from recent schedule",
    )
    verdict: Literal["SUPPORT", "NEUTRAL", "OPPOSE", "INSUFFICIENT_DATA"] = Field(
        default="NEUTRAL",
        description="Overall LLM qualitative stance toward the proposed ML signal",
    )
    confidence_adjustment: float = Field(
        default=0.0,
        ge=-0.25,
        le=0.25,
        description="Suggested bounded confidence delta for bet-manager",
    )
    reasoning: str = Field(
        ...,
        max_length=1500,
        description="Concise structured rationale",
    )
