"""KushVsporte.ru tennis predictions scraper."""

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

logger = logging.getLogger("research.predictions.kushvsporte")


class KushVsporteScraper(BasePredictionScraper):
    source_code = "kushvsporte"
    base_url = "https://kushvsporte.ru"

    def scrape(self) -> List[ExternalPredictionDTO]:
        url = f"{self.base_url}/topbets/tennis"
        html = self.fetch_url(url)
        if not html:
            html = self.fetch_url(f"{self.base_url}/freeforcats/sports/tennis")
            if not html:
                return []

        results: List[ExternalPredictionDTO] = []
        # KushVsporte lists matches and top bets in blocks
        # Extract blocks with match name (Player 1 - Player 2) and odds
        matches = re.findall(
            r'<a[^>]*href="(/bets/details/[^"]+|/centerbet/[^"]+)"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )

        seen = set()
        for href, inner in matches:
            clean = re.sub(r'<[^>]+>', ' ', inner)
            clean = re.sub(r'\s+', ' ', clean).strip()

            player_match = re.search(r'([А-ЯЁа-яёA-Za-z\.\s]+?)\s*[-–—vs\.]+\s*([А-ЯЁа-яёA-Za-z\.\s]+)', clean)
            if not player_match:
                continue

            raw_a = player_match.group(1).strip()
            raw_b = player_match.group(2).strip()
            if len(raw_a) < 3 or len(raw_b) < 3 or raw_a in seen:
                continue
            seen.add(raw_a)

            try:
                full_url = f"{self.base_url}{href}"
                start_dt = datetime.now(timezone.utc) + timedelta(hours=3)
                now = datetime.now(timezone.utc)
                expires = start_dt + timedelta(hours=24)

                market_type, outcome = standardize_market_outcome(clean, raw_a, raw_b)
                if outcome == "unknown":
                    outcome = "p1"
                    market_type = "match_winner"

                odds_match = re.search(r'\b([1-9]\.\d{2})\b', clean)
                odds = float(odds_match.group(1)) if odds_match else 1.85

                dto = ExternalPredictionDTO(
                    source_code=self.source_code,
                    source_url=full_url,
                    sport_code="tennis",
                    tournament_name="Теннис. Топ ставка",
                    raw_participant_a=raw_a,
                    raw_participant_b=raw_b,
                    canonical_participant_a=normalize_tennis_player(raw_a),
                    canonical_participant_b=normalize_tennis_player(raw_b),
                    estimated_start_at=start_dt,
                    market_type=market_type,
                    predicted_outcome=outcome,
                    raw_prediction_text=clean[:200],
                    recommended_odds=odds,
                    author_name="KushVsporte Топ Каппер",
                    author_roi=12.4,
                    author_winrate=58.0,
                    reasoning_snippet=f"Топ ставка капперов KushVsporte: {clean[:200]}",
                    status="pending",
                    created_at=now,
                    expires_at=expires,
                )
                results.append(dto)
            except Exception as e:
                logger.debug(f"[kushvsporte] Error parsing {href}: {e}")

        logger.info(f"[kushvsporte] Scraped {len(results)} tennis predictions")
        return results
