"""
Rolling on-disk history of online-training passes — val_loss/val_guess_rate/train_loss/
best_epoch used to only ever appear as ephemeral text in AI_LOGS (capped at
MAX_LOG_ENTRIES=300, lost on every container restart), with no way to see how they
moved over more than the last few passes. Mirrors backtest.py's save_and_record/
get_backtest_history pattern deliberately (own file, own cap, newest-first) — same
shape, same reasoning, just a different cadence: training passes fire far more often
than backtests, so this caps at more entries.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from app.config import MODEL_DIR

logger = logging.getLogger("ai_service_training_history")

TRAINING_HISTORY_PATH = os.path.join(MODEL_DIR, "training_runs.json")
# Training passes can fire every couple of minutes when TRAIN_EVERY_CYCLES/
# MIN_TRAIN_SAMPLES both clear — 1000 entries covers a few days of that even at the
# fastest realistic cadence, and this is cheap either way (just JSON on disk).
MAX_TRAINING_HISTORY = 1000

MOSCOW_TZ = timezone(timedelta(hours=3))


def now_iso() -> str:
    return datetime.now(MOSCOW_TZ).isoformat()


def record_training_run(entry: Dict[str, Any]) -> None:
    """Appends one training pass's summary metrics (see pipeline.py's call site for the
    exact fields) to the front of the history file, capped at MAX_TRAINING_HISTORY."""
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)
        history: List[Dict[str, Any]] = []
        if os.path.exists(TRAINING_HISTORY_PATH):
            try:
                with open(TRAINING_HISTORY_PATH, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = []
        history.insert(0, entry)
        history = history[:MAX_TRAINING_HISTORY]
        with open(TRAINING_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error persisting training run history: {e}")


def get_training_history() -> List[Dict[str, Any]]:
    if not os.path.exists(TRAINING_HISTORY_PATH):
        return []
    try:
        with open(TRAINING_HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading training run history: {e}")
        return []
