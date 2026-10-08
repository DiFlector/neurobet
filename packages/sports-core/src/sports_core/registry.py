from typing import Dict, Optional
from .base import SportAdapter


class SportRegistry:
    """Registry maintaining active sport adapters."""
    def __init__(self):
        self._adapters: Dict[str, SportAdapter] = {}

    def register(self, adapter: SportAdapter) -> None:
        self._adapters[adapter.sport_code] = adapter

    def get(self, sport_code: str) -> Optional[SportAdapter]:
        return self._adapters.get(sport_code)

    def list_sports(self) -> list[str]:
        return list(self._adapters.keys())


registry = SportRegistry()
