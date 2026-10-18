"""NB-Bet.com tennis predictions scraper."""

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

logger = logging.getLogger("research.predictions.nb_bet")


class NbBetScraper(BasePredictionScraper):
    source_code = "nb_bet"
    base_url = "https://nb-bet.com"

    def scrape(self) -> List[ExternalPredictionDTO]:
        url = f"{self.base_url}/tennis"
        html = self.fetch_url(url)
        if not html:
            return []

        results: List[ExternalPredictionDTO] = []
        # Pattern: href="(https://nb-bet\.com/tennis/events/\d+-([^"]+?)-prognoz-na-match)" ... inner text
        events = re.findall(
            r'<a[^>]*href="(https://nb-bet\.com/tennis/events/(\d+)-([^"]+?)-prognoz-na-match)"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )

        seen = set()
        for full_url, event_id, slug, inner in events:
            if event_id in seen:
                continue
            seen.add(event_id)

            try:
                clean_text = re.sub(r'<[^>]+>', ' ', inner)
                clean_text = re.sub(r'\s+', ' ', clean_text).strip()

                # Parse tournament & participants from inner text or slug
                slug_parts = slug.split("-")
                if len(slug_parts) >= 2:
                    mid = len(slug_parts) // 2
                    raw_a = " ".join(p.capitalize() for p in slug_parts[:mid])
                    raw_b = " ".join(p.capitalize() for p in slug_parts[mid:])
                else:
                    raw_a = "Player A"
                    raw_b = "Player B"

                # Parse time from text (e.g. "сегодня 04:30" or "завтра 18:00")
                time_match = re.search(r'(сегодня|завтра|\d{1,2}\s+[а-я]+)\s*(\d{1,2}:\d{2})', clean_text, re.I)
                date_str = f"{time_match.group(1)} {time_match.group(2)}" if time_match else "сегодня 12:00"
                start_dt = parse_russian_datetime(date_str)

                # Tournament
                tour_match = re.search(r'((?:ATP|WTA|Челленджер|ITF)[^A-Za-zА-Яа-я0-9]*[A-Za-zА-Яа-я0-9\s,–\-\.]+?)(?=[A-ZА-Я][a-zа-я]+\s+[A-ZА-Я]\.|\s+сегодня|\s+завтра|$)', clean_text, re.I)
                tournament = tour_match.group(1).strip() if tour_match else "Теннис. Турнир"

                now = datetime.now(timezone.utc)
                expires = start_dt + timedelta(hours=24)

                dto = ExternalPredictionDTO(
                    source_code=self.source_code,
                    source_url=full_url,
                    sport_code="tennis",
                    tournament_name=tournament[:100],
                    raw_participant_a=raw_a,
                    raw_participant_b=raw_b,
                    canonical_participant_a=normalize_tennis_player(raw_a),
                    canonical_participant_b=normalize_tennis_player(raw_b),
                    estimated_start_at=start_dt,
                    market_type="match_winner",
                    predicted_outcome="p1",
                    raw_prediction_text=f"Анализ и тренды NB-Bet ({tournament})",
                    recommended_odds=1.80,
                    author_name="NB-Bet Аналитика",
                    reasoning_snippet=f"Статистические тренды NB-Bet: {clean_text[:200]}",
                    status="pending",
                    created_at=now,
                    expires_at=expires,
                )
                results.append(dto)
            except Exception as e:
                logger.debug(f"[nb_bet] Error parsing event {event_id}: {e}")

        logger.info(f"[nb_bet] Scraped {len(results)} tennis predictions")
        return results
