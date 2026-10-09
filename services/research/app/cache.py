"""Research caching layer and execution run history."""

from datetime import datetime, timezone
import hashlib
import logging
from typing import Dict, Any, List, Optional
from contracts import ResearchPacket
from .config import config

logger = logging.getLogger("research.cache")


class ResearchCache:
    """
    In-memory and Redis-compatible cache for ResearchPackets.
    Ensures repeated requests within TTL avoid redundant network operations.
    """

    def __init__(self, ttl_seconds: int = None):
        self.ttl_seconds = ttl_seconds or config.cache_ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._history: List[Dict[str, Any]] = []

    @staticmethod
    def generate_cache_key(event_id: str, query: str) -> str:
        """Generates a stable cache key."""
        normalized_q = " ".join(query.lower().split())
        digest = hashlib.sha256(f"{event_id}:{normalized_q}".encode("utf-8")).hexdigest()[:16]
        return f"research:{event_id}:{digest}"

    def get(self, cache_key: str) -> Optional[ResearchPacket]:
        """Retrieves cached ResearchPacket if still within TTL."""
        entry = self._cache.get(cache_key)
        if not entry:
            return None

        now = datetime.now(timezone.utc)
        cached_at = entry["cached_at"]
        age = (now - cached_at).total_seconds()

        if age > self.ttl_seconds:
            logger.info(f"Cache expired for key '{cache_key}' (age {age:.1f}s > {self.ttl_seconds}s)")
            del self._cache[cache_key]
            return None

        logger.info(f"Cache HIT for key '{cache_key}' (age {age:.1f}s)")
        packet: ResearchPacket = entry["packet"]
        # Mark as cached
        return packet.model_copy(update={"cached": True})

    def set(self, cache_key: str, packet: ResearchPacket):
        """Stores ResearchPacket with timestamp."""
        self._cache[cache_key] = {
            "packet": packet,
            "cached_at": datetime.now(timezone.utc),
        }

    def record_run(self, event_id: str, query: str, packet_id: str, evidence_count: int, cache_hit: bool):
        """Records execution in run history."""
        self._history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_id": event_id,
            "query": query,
            "packet_id": packet_id,
            "evidence_count": evidence_count,
            "cache_hit": cache_hit,
        })
        # Keep recent 200 runs
        if len(self._history) > 200:
            self._history.pop(0)

    def get_history(self) -> List[Dict[str, Any]]:
        """Returns execution run history."""
        return list(reversed(self._history))

    def clear(self):
        """Clears all cached packets."""
        self._cache.clear()
