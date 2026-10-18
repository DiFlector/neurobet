"""Livesport.ru tennis tips and predictions scraper."""

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

logger = logging.getLogger("research.predictions.livesport")


class LivesportScraper(BasePredictionScraper):
    source_code = "livesport"
    base_url = "https://www.livesport.ru"

    def scrape(self) -> List[ExternalPredictionDTO]:
        url = f"{self.base_url}/tips/tennis/"
        html = self.fetch_url(url)
        if not html:
            html = self.fetch_url(f"{self.base_url}/tennis/")
            if not html:
                return []

        results: List[ExternalPredictionDTO] = []
        # Pattern: /tips/(?:tennis/)?\d{4}/\d{2}/\d{2}/([^"/]+)/
        articles = re.findall(
            r'<a[^>]*href="(/tips/(?:tennis/)?(\d{4})/(\d{2})/(\d{2})/([^"/]+)/?)"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )

        seen = set()
        for href, year, month, day, slug, inner in articles:
            if slug in seen:
                continue
            seen.add(slug)

            try:
                full_url = f"{self.base_url}{href}"
                clean = re.sub(r'<[^>]+>', ' ', inner)
                clean = re.sub(r'\s+', ' ', clean).strip()

                start_dt = datetime(int(year), int(month), int(day), 14, 0, tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                expires = start_dt + timedelta(hours=24)

                slug_parts = slug.split("-")
                if len(slug_parts) >= 2:
                    mid = len(slug_parts) // 2
                    raw_a = " ".join(p.capitalize() for p in slug_parts[:mid])
                    raw_b = " ".join(p.capitalize() for p in slug_parts[mid:])
                else:
                    raw_a = clean[:30] if clean else "Player A"
                    raw_b = "Player B"

                odds_match = re.search(r'\b([1-9]\.\d{2})\b', clean)
                odds = float(odds_match.group(1)) if odds_match else 1.80

                market_type, outcome = standardize_market_outcome(clean, raw_a, raw_b)
                if outcome == "unknown":
                    outcome = "p1"
                    market_type = "match_winner"

                dto = ExternalPredictionDTO(
                    source_code=self.source_code,
                    source_url=full_url,
                    sport_code="tennis",
                    tournament_name="Теннис. LiveSport",
                    raw_participant_a=raw_a,
                    raw_participant_b=raw_b,
                    canonical_participant_a=normalize_tennis_player(raw_a),
                    canonical_participant_b=normalize_tennis_player(raw_b),
                    estimated_start_at=start_dt,
                    market_type=market_type,
                    predicted_outcome=outcome,
                    raw_prediction_text=clean[:200] if clean else f"Прогноз LiveSport на {raw_a} - {raw_b}",
                    recommended_odds=odds,
                    author_name="LiveSport Редакция",
                    reasoning_snippet=f"Аналитика LiveSport: {clean[:200]}",
                    status="pending",
                    created_at=now,
                    expires_at=expires,
                )
                results.append(dto)
            except Exception as e:
                logger.debug(f"[livesport] Error parsing {slug}: {e}")

        logger.info(f"[livesport] Scraped {len(results)} tennis predictions")
        return results
