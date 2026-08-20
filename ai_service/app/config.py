import os

DATABASE_URL = os.environ["DATABASE_URL"]
MODEL_DIR = os.getenv("MODEL_DIR", "/app/data/models")
DEEPSEEK_TOKEN = os.getenv("DEEPSEEK_TOKEN", "")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# Optional DeepSeek LLM layer (narratives, digests, match web-search context).
# Master switch — keep off until DEEPSEEK_TOKEN is set on prod and behaviour is verified.
LLM_ENABLED = _env_bool("NEURALBET_LLM_ENABLED", "0")
LLM_MATCH_CONTEXT = _env_bool("NEURALBET_LLM_MATCH_CONTEXT", "1")
# When on: skip a live stake if web-search LLM confidently disagrees with the model.
LLM_VETO = _env_bool("NEURALBET_LLM_VETO", "0")
LLM_VETO_MIN_CONFIDENCE = float(os.getenv("NEURALBET_LLM_VETO_MIN_CONFIDENCE", "0.7"))
LLM_DIGEST_HOURS = float(os.getenv("NEURALBET_LLM_DIGEST_HOURS", "3"))
LLM_DIGEST_MAX_HISTORY = int(os.getenv("NEURALBET_LLM_DIGEST_MAX_HISTORY", "20"))
LLM_MIN_INTERVAL_SECONDS = float(os.getenv("NEURALBET_LLM_MIN_INTERVAL_SECONDS", "2"))
LLM_MAX_CONTEXT_PER_CYCLE = int(os.getenv("NEURALBET_LLM_MAX_CONTEXT_PER_CYCLE", "2"))
LLM_MAX_RATIONALE_PER_CYCLE = int(os.getenv("NEURALBET_LLM_MAX_RATIONALE_PER_CYCLE", "3"))
