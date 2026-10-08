"""Generic sport-independent participant and market domain models."""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class GenericParticipant(BaseModel):
    """Normalized sport participant (individual athlete or entire team)."""
    sport_code: str
    participant_type: Literal["individual", "team"]
    canonical_name: str
    aliases: List[str] = Field(default_factory=list)
    country: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GenericMarketDefinition(BaseModel):
    """Specification of a betting market applicable across sports or adapter-specific."""
    market_type: str
    name_ru: str
    supported_outcomes: List[str]
    requires_line_value: bool = False
    description: Optional[str] = None


class SportDescriptor(BaseModel):
    """High-level metadata and capability descriptor for a sport."""
    sport_code: str
    name_ru: str
    participant_type: Literal["individual", "team"]
    supported: bool = True
    status: Literal["ACTIVE", "UNSUPPORTED", "DEVELOPMENT"] = "ACTIVE"
    supported_markets: List[GenericMarketDefinition] = Field(default_factory=list)
    features: List[str] = Field(default_factory=list)
