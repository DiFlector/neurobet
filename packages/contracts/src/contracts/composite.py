"""Public state of the single composite shadow line."""

from typing import List, Literal, Optional

from pydantic import Field

from .base import BaseContract


class CompositeComponentVote(BaseContract):
    """One inner line. It is not a separate model in the registry."""

    component_id: str
    label: str
    side: Optional[str] = None
    confirmed: bool = False
    probability: float = 0.0
    edge: float = 0.0
    reason: str = ""
    verdict: str = ""


class CompositeModelState(BaseContract):
    """Agreement of the three pinned lines, shown as one model."""

    label: Literal["Составная модель"] = "Составная модель"
    phase: Literal["0/3", "1/3", "2/3", "3/3"]
    display_phase: str
    selected: bool
    market: str
    side: Optional[str] = None
    agreement_count: int = Field(ge=0, le=3)
    components: List[CompositeComponentVote]
