"""Football sport adapter package for Neurobet."""

import os
from sports_core import registry
from .adapter import FootballSportAdapter
from .normalizer import normalize_russian_football_team
from .pipeline import FootballPipeline

default_football_adapter = FootballSportAdapter()


def is_football_enabled() -> bool:
    """Checks environment flags to determine if football is enabled."""
    val = os.getenv("ENABLE_FOOTBALL") or os.getenv("SPORT_FOOTBALL_ENABLED") or "false"
    return str(val).strip().lower() in ("true", "1", "yes")


def register_football_adapter() -> None:
    """Explicitly registers football adapter into global sports registry."""
    registry.register(default_football_adapter)


def unregister_football_adapter() -> None:
    """Explicitly unregisters football adapter from global sports registry."""
    if hasattr(registry, "unregister"):
        registry.unregister("football")
    elif "football" in getattr(registry, "_adapters", {}):
        del registry._adapters["football"]


# Auto-register strictly if config-flag is enabled
if is_football_enabled():
    register_football_adapter()


__all__ = [
    "FootballSportAdapter",
    "FootballPipeline",
    "normalize_russian_football_team",
    "default_football_adapter",
    "is_football_enabled",
    "register_football_adapter",
    "unregister_football_adapter",
]
