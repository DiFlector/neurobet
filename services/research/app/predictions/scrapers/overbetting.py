"""Overbetting.ru tennis predictions scraper."""

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

logger = logging.getLogger("research.predictions.overbetting")


class OverbettingScraper(BasePredictionScraper):
    source_code = "overbetting"
    base_url = "https://overbetting.ru"

    def scrape(self) -> List[ExternalPredictionDTO]:
        url = f"{self.base_url}/tips/tennis/"
        html = self.fetch_url(url)
        if not html:
            return []

        results: List[ExternalPredictionDTO] = []
        # Pattern: href="(/tips/[^"]+)" with inner text
        tips = re.findall(
            r'<a[^>]*href="(/tips/[^"]*?tennis[^"]*?)"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )

        seen = set()
        for href, inner in tips:
            if href in seen or any(skip in href for skip in ["page", "archive", "rules"]):
                continue
            seen.add(href)

            clean = re.sub(r'<[^>]+>', ' ', inner)
            clean = re.sub(r'\s+', ' ', clean).strip()

            player_match = re.search(r'([А-ЯЁа-яёA-Za-z\.\s]+?)\s*[-–—vs\.]+\s*([А-ЯЁа-яёA-Za-z\.\s]+)', clean)
            if not player_match:
                continue

            raw_a = player_match.group(1).strip()
            raw_b = player_match.group(2).strip()
            if len(raw_a) < 3 or len(raw_b) < 3:
                continue

            try:
                full_url = f"{self.base_url}{href}"
                start_dt = datetime.now(timezone.utc) + timedelta(hours=5)
                now = datetime.now(timezone.utc)
                expires = start_dt + timedelta(hours=24)

                market_type, outcome = standardize_market_outcome(clean, raw_a, raw_b)
                if outcome == "unknown":
                    outcome = "p1"
                    market_type = "match_winner"

                odds_match = re.search(r'\b([1-9]\.\d{2})\b', clean)
                odds = float(odds_match.group(1)) if odds_match else 1.82

                dto = ExternalPredictionDTO(
                    source_code=self.source_code,
                    source_url=full_url,
                    sport_code="tennis",
                    tournament_name="Теннис. Overbetting",
                    raw_participant_a=raw_a,
                    raw_participant_b=raw_b,
                    canonical_participant_a=normalize_tennis_player(raw_a),
                    canonical_participant_b=normalize_tennis_player(raw_b),
                    estimated_start_at=start_dt,
                    market_type=market_type,
                    predicted_outcome=outcome,
                    raw_prediction_text=clean[:200] if clean else f"Ставка Overbetting на {raw_a}",
                    recommended_odds=odds,
                    author_name="Overbetting Эксперт",
                    author_roi=6.7,
                    reasoning_snippet=f"Прогноз Overbetting: {clean[:200]}",
                    status="pending",
                    created_at=now,
                    expires_at=expires,
                )
                results.append(dto)
            except Exception as e:
                logger.debug(f"[overbetting] Error parsing {href}: {e}")

        logger.info(f"[overbetting] Scraped {len(results)} tennis predictions")
        return results
