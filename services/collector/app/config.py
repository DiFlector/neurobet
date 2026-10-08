"""Configuration settings for FON.BET collector."""

import os
from pydantic import BaseModel, Field


class CollectorConfig(BaseModel):
    collector_version: str = "1.0.0"
    sport_code: str = os.getenv("PRIMARY_SPORT", "tennis")
    fonbet_base_url: str = os.getenv("FONBET_BASE_URL", "https://fon.bet")
    fonbet_live_url: str = os.getenv("FONBET_LIVE_URL", "https://fon.bet/live/tennis")
    language: str = "ru"
    locale: str = "ru-RU"
    timezone_id: str = "Europe/Moscow"

    # Polling & Concurrency
    poll_min_seconds: float = float(os.getenv("FONBET_POLL_MIN_SECONDS", "5.0"))
    poll_max_seconds: float = float(os.getenv("FONBET_POLL_MAX_SECONDS", "10.0"))
    max_concurrency: int = int(os.getenv("FONBET_MAX_CONCURRENCY", "4"))
    request_timeout_seconds: int = int(os.getenv("FONBET_REQUEST_TIMEOUT_SECONDS", "15"))
    headless: bool = os.getenv("COLLECTOR_HEADLESS", "true").lower() in ("true", "1", "yes")

    # MinIO / S3 Object Storage
    s3_endpoint: str = os.getenv(
        "MINIO_ENDPOINT",
        f"http://{os.getenv('MINIO_HOST', 'minio')}:{os.getenv('MINIO_INTERNAL_PORT', '8000')}",
    )
    s3_access_key: str = os.getenv("MINIO_ACCESS_KEY", "neurobet_prod_s3_admin")
    s3_secret_key: str = os.getenv("MINIO_SECRET_KEY", "Nb_S3_7kM2wP9rT4vY1bC6eH8jL3nQ5s")
    s3_bucket_raw: str = os.getenv("MINIO_BUCKET_RAW", "raw-snapshots")

    # Redis Streams
    redis_url: str = os.getenv(
        "REDIS_URL", "redis://:Nb_Rd_3vF8nK2pW7sY1tZ4mQ6bC9eH5jL@redis:6379/0"
    )


settings = CollectorConfig()
