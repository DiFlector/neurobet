"""Tennis sport adapter package for Neurobet."""

from sports_core import registry
from .adapter import TennisSportAdapter
from .normalizer import normalize_russian_player_name
from .pipeline import TennisPipeline
from .surfaces import detect_tennis_surface

# Auto-register tennis adapter in global sports registry
default_tennis_adapter = TennisSportAdapter()
registry.register(default_tennis_adapter)

__all__ = [
    "TennisSportAdapter",
    "TennisPipeline",
    "detect_tennis_surface",
    "normalize_russian_player_name",
    "default_tennis_adapter",
]
