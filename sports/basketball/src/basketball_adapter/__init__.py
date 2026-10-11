"""Basketball sport adapter package for Neurobet."""

import os
from sports_core import registry
from .adapter import BasketballSportAdapter
from .normalizer import normalize_russian_basketball_team
from .pipeline import BasketballPipeline

default_basketball_adapter = BasketballSportAdapter()


def is_basketball_enabled() -> bool:
    """Checks environment flags to determine if basketball is enabled."""
    val = os.getenv("ENABLE_BASKETBALL") or os.getenv("SPORT_BASKETBALL_ENABLED") or "false"
    return str(val).strip().lower() in ("true", "1", "yes")


def register_basketball_adapter() -> None:
    """Explicitly registers basketball adapter into global sports registry."""
    registry.register(default_basketball_adapter)


def unregister_basketball_adapter() -> None:
    """Explicitly unregisters basketball adapter from global sports registry."""
    if hasattr(registry, "unregister"):
        registry.unregister("basketball")
    elif "basketball" in getattr(registry, "_adapters", {}):
        del registry._adapters["basketball"]


# Auto-register strictly if config-flag is enabled
if is_basketball_enabled():
    register_basketball_adapter()


__all__ = [
    "BasketballSportAdapter",
    "BasketballPipeline",
    "normalize_russian_basketball_team",
    "default_basketball_adapter",
    "is_basketball_enabled",
    "register_basketball_adapter",
    "unregister_basketball_adapter",
]
