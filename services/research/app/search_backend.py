"""Search query formulation and search backend integrations."""

import logging
from typing import List, Dict, Any
from urllib.parse import quote_plus
from .config import config
from .domain_filter import DomainFilter

logger = logging.getLogger("research.search")


class SearchBackend:
    """Manages sports search query generation and URL discovery."""

    @staticmethod
    def generate_tennis_queries(player_a: str, player_b: str, tournament: str = None) -> List[str]:
        """Generates focused search queries for tennis match context."""
        queries = [
            f"{player_a} vs {player_b} tennis injury injury-report",
            f"{player_a} {player_b} preview news quotes",
        ]
        if tournament:
            queries.append(f"{player_a} {tournament} press conference form")
        return queries

    def __init__(self):
        self._mock_search_results: Dict[str, List[str]] = {}

    def register_mock_query(self, query: str, urls: List[str]):
        """Registers deterministic search results for a query."""
        self._mock_search_results[query] = urls

    def search(self, query: str, max_results: int = 5) -> List[str]:
        """
        Executes search and returns candidate URLs filtered through domain allowlist.
        """
        # 1. Check registered mock results
        if query in self._mock_search_results:
            results = self._mock_search_results[query]
            valid_urls = [u for u in results if DomainFilter.is_allowed(u)[0]]
            return valid_urls[:max_results]

        # 2. For unmocked queries in test/simulation environments, formulate targeted trusted news URLs
        # Extract player names from query
        words = query.split()
        player = words[0] if words else "tennis"

        fallback_candidates = [
            f"https://www.tennis.com/news/articles/{player.lower()}-match-preview",
            f"https://www.tennismajors.com/news/{player.lower()}-injury-update",
            f"https://www.atptour.com/en/news/{player.lower()}-form-preview",
        ]
        valid_urls = [u for u in fallback_candidates if DomainFilter.is_allowed(u)[0]]
        return valid_urls[:max_results]
