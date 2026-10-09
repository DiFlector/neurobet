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
            "bbc.com",
            "theguardian.com",
            "apnews.com",
            "sport-express.ru",
            "tennisuptodate.com",
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
