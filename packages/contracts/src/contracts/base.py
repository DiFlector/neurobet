from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """Return current timestamp in UTC timezone."""
    return datetime.now(timezone.utc)


class BaseContract(BaseModel):
    """
    Base contract for all cross-service data packets in Neurobet.
    Enforces schema versioning and UTC timestamps.
    """
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        populate_by_name=True,
    )

    schema_version: str = Field(
        default="1.0.0",
        description="Semantic version of the data contract schema",
    )
    timestamp: datetime = Field(
        default_factory=utc_now,
        description="Event observation or generation timestamp in UTC",
    )
