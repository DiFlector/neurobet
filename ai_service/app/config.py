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
# Shadow-log every LLM decision (even with veto off) and score vs live_bets outcomes.
LLM_SHADOW = _env_bool("NEURALBET_LLM_SHADOW", "1")
# Run web-search / rationales after bankroll place (don't block staking). Forced sync when veto active.
LLM_ASYNC = _env_bool("NEURALBET_LLM_ASYNC", "1")
# Auto-enable veto only when shadow report proves model+veto beats model-only (see insights).
LLM_VETO_AUTO = _env_bool("NEURALBET_LLM_VETO_AUTO", "1")
LLM_VETO_AUTO_MIN_SETTLED = int(os.getenv("NEURALBET_LLM_VETO_AUTO_MIN_SETTLED", "150"))
# Sports allowed for web-search context (* / all = every sport). Default: tennis+football
# (news/rosters exist). Table-tennis live totals get little usable search signal.
_LLM_CTX_SPORTS_RAW = os.getenv("NEURALBET_LLM_MATCH_CONTEXT_SPORTS", "теннис,футбол").strip().lower()
LLM_MATCH_CONTEXT_SPORTS: frozenset[str] | None = (
    None
    if _LLM_CTX_SPORTS_RAW in ("", "*", "all")
    else frozenset(s.strip() for s in _LLM_CTX_SPORTS_RAW.split(",") if s.strip())
)
LLM_DIGEST_HOURS = float(os.getenv("NEURALBET_LLM_DIGEST_HOURS", "3"))
LLM_DIGEST_MAX_HISTORY = int(os.getenv("NEURALBET_LLM_DIGEST_MAX_HISTORY", "20"))
LLM_MIN_INTERVAL_SECONDS = float(os.getenv("NEURALBET_LLM_MIN_INTERVAL_SECONDS", "2"))
LLM_MAX_CONTEXT_PER_CYCLE = int(os.getenv("NEURALBET_LLM_MAX_CONTEXT_PER_CYCLE", "2"))
LLM_MAX_RATIONALE_PER_CYCLE = int(os.getenv("NEURALBET_LLM_MAX_RATIONALE_PER_CYCLE", "3"))
LLM_SHADOW_MAX_DECISIONS = int(os.getenv("NEURALBET_LLM_SHADOW_MAX_DECISIONS", "2000"))
