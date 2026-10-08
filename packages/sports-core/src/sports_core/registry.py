"""Registry maintaining active sport adapters with capability checking."""

from typing import Any, Dict, List, Optional, Tuple
from .base import SportAdapter
from .labels import SportLabelProvider
from .models import GenericMarketDefinition, SportDescriptor


class SportRegistry:
    """Registry maintaining active sport adapters and validating sport capabilities."""

    def __init__(self):
        self._adapters: Dict[str, SportAdapter] = {}

    def register(self, adapter: SportAdapter) -> None:
        """Register a concrete SportAdapter."""
        self._adapters[adapter.sport_code.lower()] = adapter

    def get(self, sport_code: str) -> Optional[SportAdapter]:
        """Retrieve adapter for sport code if registered."""
        return self._adapters.get(sport_code.lower())

    def is_supported(self, sport_code: str) -> bool:
        """Check if sport has an active adapter in the system."""
        return sport_code.lower() in self._adapters

    def list_sports(self) -> List[str]:
        """List all currently registered sport codes."""
        return list(self._adapters.keys())

    def get_descriptor(self, sport_code: str) -> SportDescriptor:
        """Returns structured descriptor for any sport, indicating active or unsupported status."""
        code = sport_code.lower()
        is_supp = self.is_supported(code)
        name_ru = SportLabelProvider.get_sport_name_ru(code)

        if not is_supp:
            return SportDescriptor(
                sport_code=code,
                name_ru=name_ru,
                participant_type="team" if code in ("football", "hockey", "basketball", "volleyball") else "individual",
                supported=False,
                status="UNSUPPORTED",
                supported_markets=[],
                features=[],
            )

        adapter = self._adapters[code]
        markets: List[GenericMarketDefinition] = []
        for m_type in adapter.supported_markets():
            m_name_ru = SportLabelProvider.get_market_name_ru(code, m_type)
            markets.append(
                GenericMarketDefinition(
                    market_type=m_type,
                    name_ru=m_name_ru,
                    supported_outcomes=["player_a", "player_b"] if code == "tennis" else ["team_a", "team_b"],
                )
            )

        return SportDescriptor(
            sport_code=code,
            name_ru=name_ru,
            participant_type="individual" if code == "tennis" else "team",
            supported=True,
            status="ACTIVE",
            supported_markets=markets,
            features=["live_tracking", "hierarchical_scoring", "odds_hash_dedup"],
        )

    def validate_proposal(self, proposal: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Validates sport support and sport-specific betting rules.
        Returns (is_valid, rejection_reason).
        """
        sport_code = str(proposal.get("sport_code") or "").lower()
        if not self.is_supported(sport_code):
            return False, "UNSUPPORTED_SPORT"

        adapter = self._adapters[sport_code]
        if not adapter.validate_sport_rules(proposal):
            return False, "SPORT_RULE_VIOLATION"

        return True, None


registry = SportRegistry()
