"""
Fonbet High-Speed Data Fetcher
Fetches full line and live betting markets from Fonbet API endpoints.
"""

import httpx
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("fonbet_fetcher")

CDN_HOSTS = [
    "line-lb61-w.bk6bba-resources.com",
    "line-lb54-w.bk6bba-resources.com",
    "line-lb01.bk6bba-resources.com",
    "line01.bkfonbet.com"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://fon.bet",
    "Referer": "https://fon.bet/live"
}

class FonbetFetcher:
    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        self.client = httpx.Client(headers=HEADERS, timeout=self.timeout, follow_redirects=True)
        self.active_host = CDN_HOSTS[0]

    def fetch_live_events(self, place: str = "live") -> Dict[str, Any]:
        """
        Fetches events list from Fonbet API.
        place can be 'live', 'line', or 'all'.
        """
        endpoint = f"ma/events/listBase?lang=ru&scopeMarket=1600"
        if place == "live":
            endpoint += "&place=live"

        last_error = None
        for host in CDN_HOSTS:
            url = f"https://{host}/{endpoint}"
            try:
                logger.info(f"Querying Fonbet API: {url}")
                resp = self.client.get(url)
                if resp.status_code == 200:
                    self.active_host = host
                    data = resp.json()
                    if isinstance(data, dict) and "events" in data:
                        return data
            except Exception as e:
                logger.warning(f"Failed to fetch from {host}: {e}")
                last_error = e

        raise RuntimeError(f"Could not fetch Fonbet data from any host. Last error: {last_error}")

    def fetch_factors_catalog(self) -> List[Dict[str, Any]]:
        """Fetches factors catalogs for outcome mapping."""
        catalogs = []
        endpoints = [
            "ma/line/factorsCatalog/tables?version=0&lang=ru&sysId=21",
            "ma/line/factorsCatalog/sportBasicFactors?version=0&lang=ru&systemType=desktop",
            "ma/line/factorsCatalog/independentFactors?version=0&lang=ru&sysId=21&scopeMarket=1600"
        ]
        for ep in endpoints:
            url = f"https://{self.active_host}/{ep}"
            try:
                resp = self.client.get(url)
                if resp.status_code == 200:
                    catalogs.append(resp.json())
            except Exception as e:
                logger.warning(f"Catalog fetch error {ep}: {e}")
        return catalogs
