"""Generic sport-independent event lifecycle management."""

import enum
from typing import Set, Tuple


class EventLifecycleState(str, enum.Enum):
    """Canonical event lifecycle states for all sports in Neurobet."""
    SCHEDULED = "SCHEDULED"    # Prematch, not started
    LIVE = "LIVE"              # Actively in progress
    PAUSED = "PAUSED"          # Normal break (between sets, half-time, interval)
    SUSPENDED = "SUSPENDED"    # Abnormal delay (rain, medical, technical delay)
    FINISHED = "FINISHED"      # Match concluded normally
    CANCELLED = "CANCELLED"    # Walkover, postponed, cancelled


# Permissible lifecycle state transitions
PERMISSIBLE_TRANSITIONS: Set[Tuple[EventLifecycleState, EventLifecycleState]] = {
    # From SCHEDULED
    (EventLifecycleState.SCHEDULED, EventLifecycleState.LIVE),
    (EventLifecycleState.SCHEDULED, EventLifecycleState.SUSPENDED),
    (EventLifecycleState.SCHEDULED, EventLifecycleState.CANCELLED),

    # From LIVE
    (EventLifecycleState.LIVE, EventLifecycleState.PAUSED),
    (EventLifecycleState.LIVE, EventLifecycleState.SUSPENDED),
    (EventLifecycleState.LIVE, EventLifecycleState.FINISHED),
    (EventLifecycleState.LIVE, EventLifecycleState.CANCELLED),

    # From PAUSED
    (EventLifecycleState.PAUSED, EventLifecycleState.LIVE),
    (EventLifecycleState.PAUSED, EventLifecycleState.SUSPENDED),
    (EventLifecycleState.PAUSED, EventLifecycleState.FINISHED),
    (EventLifecycleState.PAUSED, EventLifecycleState.CANCELLED),

    # From SUSPENDED
    (EventLifecycleState.SUSPENDED, EventLifecycleState.LIVE),
    (EventLifecycleState.SUSPENDED, EventLifecycleState.PAUSED),
    (EventLifecycleState.SUSPENDED, EventLifecycleState.FINISHED),
    (EventLifecycleState.SUSPENDED, EventLifecycleState.CANCELLED),
}


class EventLifecycleManager:
    """Validates and controls generic event state transitions across any sport."""

    @staticmethod
    def is_valid_transition(
        from_state: EventLifecycleState,
        to_state: EventLifecycleState,
    ) -> bool:
        """Check if transition from from_state to to_state is valid."""
        if from_state == to_state:
            return True  # Idempotent state retention is valid
        return (from_state, to_state) in PERMISSIBLE_TRANSITIONS

    @classmethod
    def normalize_state_string(cls, raw_state: str) -> EventLifecycleState:
        """Normalize raw strings into canonical EventLifecycleState."""
        normalized = (raw_state or "").upper().strip()
        if normalized in ("PREMATCH", "SCHEDULED", "NOT_STARTED"):
            return EventLifecycleState.SCHEDULED
        if normalized in ("LIVE", "IN_PROGRESS", "ACTIVE"):
            return EventLifecycleState.LIVE
        if normalized in ("PAUSED", "BREAK", "INTERMISSION", "SET_BREAK"):
            return EventLifecycleState.PAUSED
        if normalized in ("SUSPENDED", "DELAYED", "INTERRUPTED", "RAIN_DELAY"):
            return EventLifecycleState.SUSPENDED
        if normalized in ("FINISHED", "ENDED", "COMPLETED", "CLOSED"):
            return EventLifecycleState.FINISHED
        if normalized in ("CANCELLED", "CANCELED", "POSTPONED", "ABANDONED", "WALKOVER"):
            return EventLifecycleState.CANCELLED
        return EventLifecycleState.LIVE  # Safe default for active streams
