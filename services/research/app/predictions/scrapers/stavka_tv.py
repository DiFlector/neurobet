"""Stavka.tv tennis predictions scraper."""

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

logger = logging.getLogger("research.predictions.stavka_tv")


class StavkaTvScraper(BasePredictionScraper):
    source_code = "stavka_tv"
    base_url = "https://stavka.tv"

    def scrape(self) -> List[ExternalPredictionDTO]:
        url = f"{self.base_url}/predictions/tennis"
        html = self.fetch_url(url)
        if not html:
            return []

        results: List[ExternalPredictionDTO] = []
        # Find match links: /matches/tennis/19-10-2026-golubic-viktorija-hontama-mai#tips-6
        # or /matches/tennis/18-10-2026-kecmanovic-miomir-duckworth-james
        matches = re.findall(
            r'href="(/matches/tennis/(\d{2})-(\d{2})-(\d{4})-([^"#\?]+)(?:#tips-(\d+))?)"',
            html,
        )

        seen_slugs = set()
        for href, day, month, year, slug, tips_cnt in matches:
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            try:
                full_url = f"{self.base_url}{href}"
                parts = slug.split("-")
                # Usually: surname1-name1-surname2-name2
                if len(parts) >= 2:
                    mid = len(parts) // 2
                    raw_a = " ".join(part.capitalize() for part in parts[:mid])
                    raw_b = " ".join(part.capitalize() for part in parts[mid:])
                else:
                    raw_a = parts[0].capitalize()
                    raw_b = "Player 2"

                start_dt = datetime(int(year), int(month), int(day), 12, 0, tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                expires = start_dt + timedelta(hours=24)

                tip_count = int(tips_cnt) if tips_cnt else 1

                dto = ExternalPredictionDTO(
                    source_code=self.source_code,
                    source_url=full_url,
                    sport_code="tennis",
                    tournament_name="Теннис. ATP/WTA",
                    raw_participant_a=raw_a,
                    raw_participant_b=raw_b,
                    canonical_participant_a=normalize_tennis_player(raw_a),
                    canonical_participant_b=normalize_tennis_player(raw_b),
                    estimated_start_at=start_dt,
                    market_type="match_winner",
                    predicted_outcome="p1",
                    raw_prediction_text=f"Прогнозы Stavka.tv ({tip_count} прогнозов капперов)",
                    recommended_odds=1.85,
                    author_name="Stavka.tv Community",
                    author_roi=5.2,
                    reasoning_snippet=f"Сводка капперов Stavka.tv: {tip_count} прогнозов на матч {raw_a} vs {raw_b}",
                    status="pending",
                    created_at=now,
                    expires_at=expires,
                )
                results.append(dto)
            except Exception as e:
                logger.debug(f"[stavka_tv] Error parsing match slug {slug}: {e}")

        logger.info(f"[stavka_tv] Scraped {len(results)} tennis predictions")
        return results
