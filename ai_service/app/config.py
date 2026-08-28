import os
import re
import unicodedata

DATABASE_URL = os.environ["DATABASE_URL"]
ARCHIVE_DATABASE_URL = os.getenv("ARCHIVE_DATABASE_URL", DATABASE_URL)
MODEL_DIR = os.getenv("MODEL_DIR", "/app/data/models")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

REGISTRY_DIR = os.path.join(MODEL_DIR, "registry")
ACTIVE_MODEL_PATH = os.path.join(MODEL_DIR, "active_model.json")

_DEPLOY_RAW = os.getenv("NEUROBET_DEPLOY_MODE", "prod").strip().lower()
DEPLOY_MODE = _DEPLOY_RAW if _DEPLOY_RAW in ("prod", "dev") else "prod"
IS_PROD = DEPLOY_MODE == "prod"
IS_DEV = DEPLOY_MODE == "dev"


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def get_capabilities() -> dict:
    return {
        "deploy_mode": DEPLOY_MODE,
        "training_allowed": IS_DEV,
        "backtest_allowed": IS_DEV,
        "reset_model_allowed": IS_DEV,
        "db_reset_allowed": IS_DEV,
        "model_export_allowed": IS_DEV,
        "model_upload_allowed": True,
        "model_activate_allowed": True,
        "model_create_allowed": IS_DEV,
        "archive_export_allowed": True,
        "archive_import_allowed": True,
    }
