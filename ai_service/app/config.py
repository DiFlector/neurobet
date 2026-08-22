import os

DATABASE_URL = os.environ["DATABASE_URL"]
MODEL_DIR = os.getenv("MODEL_DIR", "/app/data/models")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")
