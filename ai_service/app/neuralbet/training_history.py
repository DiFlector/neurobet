"""
Rolling on-disk history of online-training passes — val_loss/val_guess_rate/train_loss/
best_epoch used to only ever appear as a short rolling window in AI_LOGS (capped at
MAX_LOG_ENTRIES=300), with no way to see how they
moved over more than the last few passes. Mirrors backtest.py's save_and_record/
get_backtest_history pattern deliberately (own file, own cap, newest-first) — same
shape, same reasoning, just a different cadence: training passes fire far more often
than backtests, so this caps at more entries.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

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


# Chart fields: on reject, copy from last accepted so the admin trend chart
# tracks the live weights, not the rejected attempt. Attempted values are kept
# separately (val_loss_attempted already from model; train_*_attempted below).
_CARRY_FIELDS = ("val_loss", "train_loss", "val_guess_rate", "train_guess_rate")


def last_saved_run(history: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Newest pass whose weights actually landed on disk. Rows from before the
    checkpoint gate have no `checkpoint_accepted` flag and always wrote the file."""
    for row in history:
        if row.get("checkpoint_accepted") is False:
            continue
        if row.get("val_loss") is None:
            continue
        return row
    return None


def record_training_run(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Appends one training pass's summary metrics (see pipeline.py's call site for the
    exact fields) to the front of the history file, capped at MAX_TRAINING_HISTORY.
    A rejected pass still gets a new timestamped point, but chart val/train loss are
    copied from the last saved checkpoint — 0.18 stayed 0.18, not the 0.19 attempt
    and not a fresh eval of the same weights on a different val split. Attempted
    train metrics are preserved as *_attempted before the carry."""
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)
        history: List[Dict[str, Any]] = []
        if os.path.exists(TRAINING_HISTORY_PATH):
            try:
                with open(TRAINING_HISTORY_PATH, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = []
        if entry.get("checkpoint_accepted") is False:
            if entry.get("train_loss_attempted") is None and entry.get("train_loss") is not None:
                entry["train_loss_attempted"] = entry["train_loss"]
            if entry.get("train_guess_rate_attempted") is None and entry.get("train_guess_rate") is not None:
                entry["train_guess_rate_attempted"] = entry["train_guess_rate"]
            if entry.get("val_guess_rate_attempted") is None and entry.get("val_guess_rate") is not None:
                entry["val_guess_rate_attempted"] = entry["val_guess_rate"]
            prev = last_saved_run(history)
            if prev is not None:
                for field in _CARRY_FIELDS:
                    if prev.get(field) is not None:
                        entry[field] = prev[field]
        history.insert(0, entry)
        history = history[:MAX_TRAINING_HISTORY]
        with open(TRAINING_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error persisting training run history: {e}")
    return entry


def get_training_history() -> List[Dict[str, Any]]:
    if not os.path.exists(TRAINING_HISTORY_PATH):
        return []
    try:
        with open(TRAINING_HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading training run history: {e}")
        return []


def clear_training_history() -> None:
    """Wipes training_runs.json so the admin val_loss chart starts empty after a model reset."""
    try:
        if os.path.exists(TRAINING_HISTORY_PATH):
            os.remove(TRAINING_HISTORY_PATH)
    except Exception as e:
        logger.error(f"Error clearing training run history: {e}")
