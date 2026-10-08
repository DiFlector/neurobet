from typing import Protocol, List, Dict, Any, Optional
from datetime import datetime


class SportAdapter(Protocol):
    """
    Protocol implemented by every sport-specific module in Neurobet.
    Ensures complete isolation of sport logic from generic orchestration.
    """
    sport_code: str

    def supported_markets(self) -> List[str]:
        """Returns list of canonical market types supported by this sport adapter."""
        ...

    def parse_event(self, raw_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalizes raw Fonbet event data into canonical Event representation."""
        ...

    def parse_state(self, raw_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Builds hierarchical live state (sets, games, points, server)."""
        ...

    def validate_sport_rules(self, proposal: Dict[str, Any]) -> bool:
        """Sport-specific validation before a bet can be registered."""
        ...
