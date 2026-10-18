"""Coordinator for orchestrating prediction scrapers, matching, and 24h cleanup."""

from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional

from contracts import ExternalPredictionDTO
from .manager import PredictionManager
from .scrapers import ALL_SCRAPERS

logger = logging.getLogger("research.predictions.coordinator")


class PredictionCoordinator:
    """Orchestrates periodic collection of predictions across all portals."""

    def __init__(self, manager: Optional[PredictionManager] = None):
        self.manager = manager or PredictionManager()
        self.scrapers = [scraper_cls() for scraper_cls in ALL_SCRAPERS]

    def run_cycle(self) -> Dict[str, any]:
        """
        Executes a complete scraping cycle across all 9 portals:
        1. Scrapes predictions from each site.
        2. Persists and deduplicates them in database.
        3. Matches predictions to active/prematch Fonbet events.
        4. Cleans up predictions past 24h whose matches never appeared.
        """
        start_time = datetime.now(timezone.utc)
        logger.info(f"Starting predictions scraping cycle across {len(self.scrapers)} sources...")

        from concurrent.futures import ThreadPoolExecutor, as_completed

        all_dtos: List[ExternalPredictionDTO] = []
        per_source_counts: Dict[str, int] = {}

        def _run_scraper(scraper):
            source = scraper.source_code
            try:
                dtos = scraper.scrape()
                return source, dtos
            except Exception as e:
                logger.error(f"[{source}] Scraper encountered error: {e}")
                return source, []

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_run_scraper, scraper): scraper.source_code for scraper in self.scrapers}
            for fut in as_completed(futures, timeout=15):
                try:
                    source, dtos = fut.result()
                    per_source_counts[source] = len(dtos)
                    all_dtos.extend(dtos)
                    logger.info(f"[{source}] Collected {len(dtos)} predictions")
                except Exception as e:
                    src_name = futures[fut]
                    logger.warning(f"[{src_name}] Scraper execution timed out or failed: {e}")
                    per_source_counts[src_name] = 0

        # Persist and match
        saved_count = self.manager.save_predictions(all_dtos)

        # Cleanup expired (past 24h and unmatched)
        cleaned_count = self.manager.cleanup_expired_unmatched_predictions()

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        logger.info(
            f"Predictions cycle finished in {elapsed:.2f}s: "
            f"scraped={len(all_dtos)}, saved_new={saved_count}, cleaned_expired={cleaned_count}"
        )

        return {
            "total_scraped": len(all_dtos),
            "saved_new": saved_count,
            "cleaned_expired": cleaned_count,
            "per_source": per_source_counts,
            "elapsed_seconds": round(elapsed, 2),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
