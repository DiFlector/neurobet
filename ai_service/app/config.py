import os

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/autobet.db")
FINISHED_DB_PATH = os.path.join(os.path.dirname(DB_PATH), "autobet_finished.db")
MODEL_DIR = os.getenv("MODEL_DIR", "/app/data/models")
DEEPSEEK_TOKEN = os.getenv("DEEPSEEK_TOKEN", "")
