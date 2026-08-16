import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

# Path constants
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent

class Settings(BaseSettings):
    # App Information
    APP_NAME: str = "NeuroBet Fonbet LIVE Parser"
    DEBUG: bool = False

    # DeepSeek / AI Integration
    DEEPSEEK_TOKEN: str = ""

    # Database Settings
    DATABASE_URL: str = "postgresql://autobet:autobet@postgres:5432/autobet"

    # Scraper Settings
    SCRAPE_INTERVAL_SECONDS: int = 15

    # Finish detection (grace period before an event is considered finished).
    # A live event is only finalized once it has been missing from the parser's
    # snapshot for EVENT_MISS_THRESHOLD consecutive polls AND at least
    # EVENT_MISS_GRACE_MINUTES minutes have passed since it first went missing.
    #
    # Not sport-dependent: confirmed against live data (see backend/database.py's
    # save_parsed_events) that Fonbet never removes a *paused* event from the live
    # feed — a break just freezes its timer (timerDirection: 0) while the event stays
    # present, which resets miss_count back to 0 every cycle regardless of sport. Only
    # a genuinely finished match disappears from the feed entirely. So disappearance
    # itself already means "over," for every sport — no need to wait out a real
    # intermission that was never going to trigger this in the first place.
    EVENT_MISS_THRESHOLD: int = 1
    EVENT_MISS_GRACE_MINUTES: int = 1
    # Snapshot sanity guard, to avoid finalizing events on a parser/API hiccup: an
    # almost-empty snapshot is skipped entirely (see save_parsed_events).
    MIN_SNAPSHOT_EVENTS: int = 5

    # CORS Settings
    CORS_ORIGINS: List[str] = ["*"]

    model_config = SettingsConfigDict(
        env_file=(
            str(PROJECT_ROOT / ".env"),
            str(BACKEND_DIR / ".env"),
            ".env"
        ),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
