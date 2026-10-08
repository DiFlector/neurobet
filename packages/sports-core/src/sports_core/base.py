"""Sport Adapter protocol and core abstractions for Neurobet."""

from typing import Any, Dict, List, Optional, Protocol, Tuple
from contracts import Event, EventState, OddsSnapshot, Settlement


class SportAdapter(Protocol):
    """
    Protocol implemented by every sport-specific module in Neurobet.
    Ensures complete isolation of sport logic from generic orchestration.
    """

    sport_code: str

    def supported_markets(self) -> List[str]:
        """Returns list of canonical market types supported by this sport adapter."""
        ...

    def parse_event(self, raw_payload: Dict[str, Any]) -> Event:
        """Normalizes raw Fonbet event data into canonical Event contract."""
        ...

    def parse_state(self, raw_payload: Dict[str, Any]) -> EventState:
        """Builds hierarchical live state (sets, games, points, server)."""
        ...

    def parse_odds(self, raw_payload: Dict[str, Any]) -> OddsSnapshot:
        """Extracts canonical markets, selections, and odds snapshot."""
        ...

    def parse_result(self, raw_payload: Dict[str, Any]) -> Optional[Settlement]:
        """Parses match conclusion and produces Settlement contract if match is finished."""
        ...

    def detect_surface(self, tournament: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Determines playing surface (e.g. hard, clay, grass, carpet)."""
        ...

    def normalize_player_name(self, raw_name: str) -> str:
        """Normalizes participant name to canonical Russian form."""
        ...

    def validate_sport_rules(self, proposal: Dict[str, Any]) -> bool:
        """Sport-specific validation before a bet can be registered."""
        ...
