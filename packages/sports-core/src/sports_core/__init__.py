"""Sports Core package: sport-independent contracts, lifecycle, registry, models and labels."""

from .base import SportAdapter
from .labels import SportLabelProvider
from .lifecycle import EventLifecycleManager, EventLifecycleState
from .models import GenericMarketDefinition, GenericParticipant, SportDescriptor
from .registry import SportRegistry, registry

__all__ = [
    "SportAdapter",
    "SportRegistry",
    "registry",
    "EventLifecycleState",
    "EventLifecycleManager",
    "GenericParticipant",
    "GenericMarketDefinition",
    "SportDescriptor",
    "SportLabelProvider",
]
