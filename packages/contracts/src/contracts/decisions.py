from datetime import datetime
from typing import Literal, Optional
from pydantic import Field
from .base import BaseContract


class LLMDecision(BaseContract):
    """
    Structured analytical output from local LLM.
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
        max_length=1000,
        description="Concise structured rationale",
    )
