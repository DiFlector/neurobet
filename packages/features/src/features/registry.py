"""Feature Builder Registry for Neurobet sports."""

from typing import Any, Dict, List, Optional
import uuid
from datetime import datetime

from contracts import FeatureVector
from .base import FeatureBuilder
from .tennis import TennisFeatureBuilder


class FeatureBuilderRegistry:
    """Registry maintaining active feature builders mapped by sport code."""

    def __init__(self):
        self._builders: Dict[str, FeatureBuilder] = {}

    def register(self, builder: FeatureBuilder) -> None:
        self._builders[builder.sport_code.lower()] = builder

    def get(self, sport_code: str) -> Optional[FeatureBuilder]:
        return self._builders.get(sport_code.lower())

    def build(
        self,
        event_id: str | uuid.UUID,
        sport_code: str,
        cutoff_time: datetime,
        event_snapshots: List[Any],
        odds_snapshots: List[Any],
    ) -> FeatureVector:
        """Dispatches feature building to the registered sport builder."""
        builder = self.get(sport_code)
        if not builder:
            # Fallback to tennis builder for default live processing
            builder = self._builders.get("tennis")
            if not builder:
                raise ValueError(f"No feature builder registered for sport '{sport_code}'")

        return builder.build_features(
            event_id=event_id,
            cutoff_time=cutoff_time,
            event_snapshots=event_snapshots,
            odds_snapshots=odds_snapshots,
        )


registry = FeatureBuilderRegistry()
registry.register(TennisFeatureBuilder())
