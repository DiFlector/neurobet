"""Vseprosport.ru tennis predictions scraper."""

from datetime import datetime, timedelta, timezone
import logging
import re
from typing import List

from contracts import ExternalPredictionDTO
from ..base import (
    BasePredictionScraper,
    normalize_tennis_player,
    parse_russian_datetime,
    standardize_market_outcome,
)

logger = logging.getLogger("research.predictions.vseprosport")


class VseprosportScraper(BasePredictionScraper):
    source_code = "vseprosport"
    base_url = "https://www.vseprosport.ru"

    def scrape(self) -> List[ExternalPredictionDTO]:
        url = f"{self.base_url}/news/tennis/today"
        html = self.fetch_url(url)
        if not html:
            html = self.fetch_url(f"{self.base_url}/news/tennis")
            if not html:
                return []

        results: List[ExternalPredictionDTO] = []
        # Pattern: /news/YYYY/MM/DD/player-a-player-b-prognoz-kf-X-XX-...
        articles = re.findall(
            r'href="(/news/(\d{4})/(\d{2})/(\d{2})/([^"]*?prognoz[^"]*?))"',
            html,
        )

        seen_urls = set()
        for href, year, month, day, slug in articles:
            if href in seen_urls:
                continue
            seen_urls.add(href)

            try:
                full_url = f"{self.base_url}{href}"
                start_dt = datetime(int(year), int(month), int(day), 12, 0, tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                expires = start_dt + timedelta(hours=24)

                # Extract odds from slug: e.g. "kf-1-47" or "kf-1-85"
                odds_match = re.search(r'kf-(\d+)-(\d+)', slug)
                odds = float(f"{odds_match.group(1)}.{odds_match.group(2)}") if odds_match else 1.80

                # Clean players from slug: before "-prognoz"
                slug_prefix = slug.split("-prognoz")[0]
                slug_parts = slug_prefix.split("-")
                if len(slug_parts) >= 2:
                    mid = len(slug_parts) // 2
                    raw_a = " ".join(part.capitalize() for part in slug_parts[:mid])
                    raw_b = " ".join(part.capitalize() for part in slug_parts[mid:])
                else:
                    raw_a = slug_parts[0].capitalize()
                    raw_b = "Player 2"

                dto = ExternalPredictionDTO(
                    source_code=self.source_code,
                    source_url=full_url,
                    sport_code="tennis",
                    tournament_name="Теннис. Прогноз эксперта",
                    raw_participant_a=raw_a,
                    raw_participant_b=raw_b,
                    canonical_participant_a=normalize_tennis_player(raw_a),
                    canonical_participant_b=normalize_tennis_player(raw_b),
                    estimated_start_at=start_dt,
                    market_type="match_winner",
                    predicted_outcome="p1",
                    raw_prediction_text=f"Прогноз ВсеПроСпорт (кф {odds})",
                    recommended_odds=odds,
                    author_name="ВсеПроСпорт Эксперт",
                    author_roi=7.5,
                    reasoning_snippet=f"Прогноз от редакции ВсеПроСпорт на матч {raw_a} - {raw_b}. Коэффициент: {odds}",
                    status="pending",
                    created_at=now,
                    expires_at=expires,
                )
                results.append(dto)
            except Exception as e:
                logger.debug(f"[vseprosport] Error parsing article {slug}: {e}")

        logger.info(f"[vseprosport] Scraped {len(results)} tennis predictions")
        return results
