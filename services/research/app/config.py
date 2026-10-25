"""Configuration for Web Research Service."""

import os
from typing import List, Set


class ResearchConfig:
    """Web research settings and security boundaries."""

    def __init__(self):
        self.enabled: bool = os.getenv("RESEARCH_ENABLED", "true").lower() in ("true", "1", "yes")
        self.max_pages: int = int(os.getenv("RESEARCH_MAX_PAGES", "5"))
        self.max_text_size: int = int(os.getenv("RESEARCH_MAX_TEXT_SIZE", "30000"))
        self.timeout_seconds: float = float(os.getenv("RESEARCH_TIMEOUT_SECONDS", "10.0"))
        self.cache_ttl_seconds: int = int(os.getenv("RESEARCH_CACHE_TTL_SECONDS", "3600"))
        # ddg | searxng | hybrid (оба параллельно) | mock
        self.search_backend: str = os.getenv("RESEARCH_SEARCH_BACKEND", "hybrid").strip().lower()
        self.searxng_url: str = os.getenv("RESEARCH_SEARXNG_URL", "").strip()
        self.persist_to_db: bool = os.getenv("RESEARCH_PERSIST_DB", "true").lower() in (
            "true",
            "1",
            "yes",
        )
        self.flashscore_tennis_profile_urls: List[str] = [
            value.strip()
            for value in os.getenv("FLASHSCORE_TENNIS_PROFILE_URLS", "").split(",")
            if value.strip()
        ]
        # Static profile batches are not a substitute for coverage-driven
        # refreshes. Keep them off unless an operator explicitly opts in.
        self.flashscore_static_profile_archive_enabled: bool = os.getenv(
            "FLASHSCORE_STATIC_PROFILE_ARCHIVE_ENABLED", "false"
        ).strip().casefold() in {"1", "true", "yes", "on"}
        self.flashscore_tennis_request_delay_seconds: float = max(
            2.0, float(os.getenv("FLASHSCORE_TENNIS_REQUEST_DELAY_SECONDS", "2.0"))
        )
        self.flashscore_tennis_profile_concurrency: int = max(
            1, int(os.getenv("FLASHSCORE_TENNIS_PROFILE_CONCURRENCY", "1"))
        )
        self.flashscore_tennis_point_poll_seconds: int = max(
            10, int(os.getenv("FLASHSCORE_TENNIS_POINT_POLL_SECONDS", "30"))
        )
        self.flashscore_tennis_point_concurrency: int = max(
            1, int(os.getenv("FLASHSCORE_TENNIS_POINT_CONCURRENCY", "1"))
        )

        # MinIO
        self.minio_host: str = os.getenv("MINIO_HOST", "minio")
        self.minio_port: int = int(os.getenv("MINIO_INTERNAL_PORT", "9000"))
        self.minio_access_key: str = os.getenv("MINIO_ACCESS_KEY", "neurobet_minio")
        self.minio_secret_key: str = os.getenv("MINIO_SECRET_KEY", "neurobet_minio_secret_pass")
        self.minio_bucket: str = os.getenv("MINIO_BUCKET_RESEARCH", "research-documents")

        # Domain Allowlist: respected news and sports media
        self.allowlist: Set[str] = {
            "atptour.com",
            "wtatennis.com",
            "tennis.com",
            "tennismajors.com",
            "reuters.com",
            "espn.com",
            "sports.ru",
            "championat.com",
            "flashscore.com",
            "flashscore.com.ua",
            "bbc.com",
            "theguardian.com",
            "apnews.com",
            "sport-express.ru",
            "tennisuptodate.com",
            "tennisabstract.com",
        }

        # Domain Denylist: betting sites, prediction-selling sites, shady aggregators
        self.denylist: Set[str] = {
            "fon.bet",
            "fonbet.ru",
            "1xbet.com",
            "bet365.com",
            "marathonbet.com",
            "vprognoze.ru",
            "kushvsporte.ru",
            "ironbets.ru",
        }


config = ResearchConfig()
