"""Betzona.ru tennis predictions scraper."""

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

logger = logging.getLogger("research.predictions.betzona")


class BetzonaScraper(BasePredictionScraper):
    source_code = "betzona"
    base_url = "https://betzona.ru"

    def scrape(self) -> List[ExternalPredictionDTO]:
        url = f"{self.base_url}/bets-sport/prognozi-na-tennis"
        html = self.fetch_url(url)
        if not html:
            return []

        results: List[ExternalPredictionDTO] = []
        # Match cards pattern:
        # href="(/.*?-prognoz-\d+\.html)" ... text contains players, tournament, date, odds
        card_pattern = re.findall(
            r'<a[^>]*href="(/[^"]*?-prognoz-\d+\.html)"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )

        seen_urls = set()
        for href, inner in card_pattern:
            if href in seen_urls:
                continue
            seen_urls.add(href)

            try:
                full_url = f"{self.base_url}{href}"
                clean_text = re.sub(r'<[^>]+>', ' ', inner)
                clean_text = re.sub(r'\s+', ' ', clean_text).strip()

                # Ensure it's tennis (ignore hockey/football widgets)
                if not any(k in clean_text.lower() for k in ["теннис", "atp", "wta", "itf", "челленджер"]):
                    continue

                # Extract participants: "М.Арнальди - Л.Музетти" or "Арнальди - Музетти"
                match_players = re.search(r'([А-ЯЁа-яёA-Za-z\.\s]+?)\s*[-–—]\s*([А-ЯЁа-яёA-Za-z\.\s]+?)(?=\s+\d|\s+Теннис|\s+ATP|\s+WTA|$)', clean_text)
                if not match_players:
                    continue
                raw_a = match_players.group(1).strip()
                raw_b = match_players.group(2).strip()

                # Tournament
                tour_match = re.search(r'(Теннис\.\s*(?:ATP|WTA|ITF|Челленджер)[^\d\.]*)', clean_text, re.I)
                tournament = tour_match.group(1).strip() if tour_match else "Теннис"

                # Estimated time
                time_match = re.search(r'(\d{1,2}\s+[а-я]+(?:\s+\d{1,2}:\d{2})?)', clean_text, re.I)
                date_str = time_match.group(1) if time_match else "сегодня 12:00"
                start_dt = parse_russian_datetime(date_str)

                # Odds
                odds_match = re.search(r'\b([1-9]\.\d{2})\b', clean_text)
                odds = float(odds_match.group(1)) if odds_match else 1.85

                # Outcome & market
                market_type, outcome = standardize_market_outcome(clean_text, raw_a, raw_b)
                if outcome == "unknown":
                    outcome = "p1"
                    market_type = "match_winner"

                now = datetime.now(timezone.utc)
                expires = start_dt + timedelta(hours=24)

                dto = ExternalPredictionDTO(
                    source_code=self.source_code,
                    source_url=full_url,
                    sport_code="tennis",
                    tournament_name=tournament,
                    raw_participant_a=raw_a,
                    raw_participant_b=raw_b,
                    canonical_participant_a=normalize_tennis_player(raw_a),
                    canonical_participant_b=normalize_tennis_player(raw_b),
                    estimated_start_at=start_dt,
                    market_type=market_type,
                    predicted_outcome=outcome,
                    raw_prediction_text=clean_text[:200],
                    recommended_odds=odds,
                    author_name="Betzona Эксперт",
                    reasoning_snippet=f"Прогноз Betzona на {raw_a} - {raw_b}. Коэффициент: {odds}",
                    status="pending",
                    created_at=now,
                    expires_at=expires,
                )
                results.append(dto)
            except Exception as e:
                logger.debug(f"[betzona] Error parsing card {href}: {e}")

        logger.info(f"[betzona] Scraped {len(results)} tennis predictions")
        return results
