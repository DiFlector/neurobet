"""Hockey sport adapter package for Neurobet."""

import os
from sports_core import registry
from .adapter import HockeySportAdapter
from .normalizer import normalize_russian_hockey_team
from .pipeline import HockeyPipeline

default_hockey_adapter = HockeySportAdapter()


def is_hockey_enabled() -> bool:
    """Checks environment flags to determine if hockey is enabled."""
    val = os.getenv("ENABLE_HOCKEY") or os.getenv("SPORT_HOCKEY_ENABLED") or "false"
    return str(val).strip().lower() in ("true", "1", "yes")


def register_hockey_adapter() -> None:
    """Explicitly registers hockey adapter into global sports registry."""
    registry.register(default_hockey_adapter)


def unregister_hockey_adapter() -> None:
    """Explicitly unregisters hockey adapter from global sports registry."""
    if hasattr(registry, "unregister"):
        registry.unregister("hockey")
    elif "hockey" in getattr(registry, "_adapters", {}):
        del registry._adapters["hockey"]


# Auto-register strictly if config-flag is enabled
if is_hockey_enabled():
    register_hockey_adapter()


__all__ = [
    "HockeySportAdapter",
    "HockeyPipeline",
    "normalize_russian_hockey_team",
    "default_hockey_adapter",
    "is_hockey_enabled",
    "register_hockey_adapter",
    "unregister_hockey_adapter",
]
