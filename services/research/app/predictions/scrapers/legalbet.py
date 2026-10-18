"""Legalbet.ru tennis tips scraper."""

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

logger = logging.getLogger("research.predictions.legalbet")


class LegalbetScraper(BasePredictionScraper):
    source_code = "legalbet"
    base_url = "https://legalbet.ru"

    def scrape(self) -> List[ExternalPredictionDTO]:
        url = f"{self.base_url}/tips/sport-tennis/"
        html = self.fetch_url(url)
        if not html:
            return []

        results: List[ExternalPredictionDTO] = []
        # Find tips links: href="(/tips/[^"]+)" with text
        # Usually: /tips/player1-player2-... or /tips/12345/
        tips = re.findall(
            r'<a[^>]*href="(/tips/[a-zA-Z0-9\-]+(?:-[a-zA-Z0-9\-]+)*-[^"#/]+/?)"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )

        seen = set()
        for href, inner in tips:
            if any(skip in href for skip in ["sport-", "archive", "today", "tomorrow", "page"]):
                continue
            clean = re.sub(r'<[^>]+>', ' ', inner)
            clean = re.sub(r'\s+', ' ', clean).strip()

            player_match = re.search(r'([А-ЯЁа-яёA-Za-z\.\s]+?)\s*[-–—]\s*([А-ЯЁа-яёA-Za-z\.\s]+)', clean)
            if not player_match:
                # Try parsing from URL slug: e.g. /tips/alcaraz-sinner-19-10-2026/
                slug = href.strip("/").split("/")[-1]
                parts = slug.split("-")
                if len(parts) >= 2:
                    mid = len(parts) // 2
                    raw_a = " ".join(p.capitalize() for p in parts[:mid])
                    raw_b = " ".join(p.capitalize() for p in parts[mid:])
                else:
                    continue
            else:
                raw_a = player_match.group(1).strip()
                raw_b = player_match.group(2).strip()

            if raw_a in seen or len(raw_a) < 3 or len(raw_b) < 3:
                continue
            seen.add(raw_a)

            try:
                full_url = f"{self.base_url}{href}"
                start_dt = datetime.now(timezone.utc) + timedelta(hours=6)
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
                    tournament_name="Теннис. Прогноз Legalbet",
                    raw_participant_a=raw_a,
                    raw_participant_b=raw_b,
                    canonical_participant_a=normalize_tennis_player(raw_a),
                    canonical_participant_b=normalize_tennis_player(raw_b),
                    estimated_start_at=start_dt,
                    market_type=market_type,
                    predicted_outcome=outcome,
                    raw_prediction_text=clean[:200] if clean else f"Ставка эксперта Legalbet на {raw_a}",
                    recommended_odds=odds,
                    author_name="Legalbet Эксперт",
                    author_roi=8.8,
                    reasoning_snippet=f"Экспертный прогноз Legalbet: {clean[:200]}",
                    status="pending",
                    created_at=now,
                    expires_at=expires,
                )
                results.append(dto)
            except Exception as e:
                logger.debug(f"[legalbet] Error parsing {href}: {e}")

        logger.info(f"[legalbet] Scraped {len(results)} tennis predictions")
        return results
