"""Volleyball sport adapter package for Neurobet."""

import os
from sports_core import registry
from .adapter import VolleyballSportAdapter
from .normalizer import normalize_russian_volleyball_team
from .pipeline import VolleyballPipeline

default_volleyball_adapter = VolleyballSportAdapter()


def is_volleyball_enabled() -> bool:
    """Checks environment flags to determine if volleyball is enabled."""
    val = os.getenv("ENABLE_VOLLEYBALL") or os.getenv("SPORT_VOLLEYBALL_ENABLED") or "false"
    return str(val).strip().lower() in ("true", "1", "yes")


def register_volleyball_adapter() -> None:
    """Explicitly registers volleyball adapter into global sports registry."""
    registry.register(default_volleyball_adapter)


def unregister_volleyball_adapter() -> None:
    """Explicitly unregisters volleyball adapter from global sports registry."""
    if hasattr(registry, "unregister"):
        registry.unregister("volleyball")
    elif "volleyball" in getattr(registry, "_adapters", {}):
        del registry._adapters["volleyball"]


# Auto-register strictly if config-flag is enabled
if is_volleyball_enabled():
    register_volleyball_adapter()


__all__ = [
    "VolleyballSportAdapter",
    "VolleyballPipeline",
    "normalize_russian_volleyball_team",
    "default_volleyball_adapter",
    "is_volleyball_enabled",
    "register_volleyball_adapter",
    "unregister_volleyball_adapter",
]
