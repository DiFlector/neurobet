import os

DATABASE_URL = os.environ["DATABASE_URL"]
MODEL_DIR = os.getenv("MODEL_DIR", "/app/data/models")
DEEPSEEK_TOKEN = os.getenv("DEEPSEEK_TOKEN", "")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
