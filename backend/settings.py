import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

# Path constants
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent

class Settings(BaseSettings):
    # App Information
    APP_NAME: str = "Autobet Fonbet LIVE Parser"
    DEBUG: bool = False

    # DeepSeek / AI Integration
    DEEPSEEK_TOKEN: str = ""

    # Database Settings
    DATABASE_PATH: str = str(PROJECT_ROOT / "data" / "autobet.db")

    # Scraper Settings
    SCRAPE_INTERVAL_SECONDS: int = 60

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
