import json
import logging
import math
import os
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import torch

from app.core.database import (
    get_connection,
    get_finished_connection,
    release_connection,
    save_ai_predictions,
)
from app.neuralbet import bankroll
from app.neuralbet.calibration import calibrate_probability, get_calibration_buckets
from app.neuralbet.context import (
    overround_group_key,
    in_train_universe,
)
from neurobet_features import (
    accumulate_overround,
    apply_sibling_coherence,
    build_model_input,
    build_team_form_index,
    build_team_stats_index,
    live_sample,
    parse_score_diff,
    parse_score_sum,
    parse_ts_epoch,
    row_to_sample,
    set_team_form_cache,
    set_team_stats_cache,
)
from neurobet_features.view import VAL_CUTOFF_SEED
from neurobet_filters import (
    universe_sql,
    universe_sql_params,
    live_gate_skip_reason,
    outcome_will_win,
    MIN_BET_COEFF,
    MAX_BET_COEFF,
    MAX_BET_COEFF_HIGH_P,
    HIGH_P_STAKE,
    MIN_BET_EDGE_PCT,
    MIN_MARKET_SUPPORT,
    FAST_FORMAT_SPORT_SQL,
    effective_live_stake_sports,
)
from app.config import MODEL_DIR
from app.neuralbet.model import NeuralBetEnsemble, MAX_EPOCHS
from app.neuralbet.training_history import record_training_run, get_training_history, clear_training_history

logger = logging.getLogger("ai_service_pipeline")

MOSCOW_TZ = timezone(timedelta(hours=3))


def now_moscow() -> datetime:
    return datetime.now(MOSCOW_TZ)


ensemble_engine = NeuralBetEnsemble()

# NeuralBetEnsemble is a module-level singleton mutated in place by every request
# handler (switches between .train()/.eval() mode, steps its optimizer). Two overlapping
# POST /predict-and-train calls would otherwise interleave those mutations and corrupt
# training. Everything that touches ensemble_engine goes through this lock.
_engine_lock = threading.Lock()
# Set by reset_neural_network *before* it waits on _engine_lock, so an in-flight
# predict-and-train / backtest can drop out at the next minibatch or cancelled SQL
# instead of making the admin "Обнулить" button hang until the whole pass finishes
# (Next/nginx then 504s and the UI shows "Ошибка при обнулении" even though the
# wipe still runs a minute later).
_abort_cycle = threading.Event()
_tracked_conns_lock = threading.Lock()
_tracked_conns: list = []


def cycle_aborted() -> bool:
    return _abort_cycle.is_set()


def _track_conn(conn):
    with _tracked_conns_lock:
        _tracked_conns.append(conn)
    return conn


def _untrack_conn(conn):
    with _tracked_conns_lock:
        try:
            _tracked_conns.remove(conn)
        except ValueError:
            pass


def _release_tracked_conns() -> None:
    with _tracked_conns_lock:
        conns = list(_tracked_conns)
        _tracked_conns.clear()
    for conn in conns:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            release_connection(conn)
        except Exception:
            pass


def abort_in_flight_cycle() -> None:
    """Stop inference, training and backtest so a model reset can take the engine lock."""
    _abort_cycle.set()
    with _tracked_conns_lock:
        conns = list(_tracked_conns)
    for conn in conns:
        try:
            conn.cancel()
        except Exception as e:
            logger.warning(f"Could not cancel in-flight DB query during reset: {e}")


def _is_query_canceled(exc: BaseException) -> bool:
    try:
        from psycopg2 import errors as pg_errors
        if isinstance(exc, pg_errors.QueryCanceled):
            return True
    except Exception:
        pass
    msg = str(exc).lower()
    return "canceling statement" in msg or "querycanceled" in msg


# Defaults only apply when no saved file exists yet. After an admin toggle the values
# live in AI_SETTINGS_PATH on the /app/data volume so a container restart does not
# flip inference/training back on.
AI_SETTINGS = {
    "ai_enabled": True,
    "training_enabled": False,
    "quality_gate_bypass": False,
}
AI_SETTINGS_PATH = os.path.join(MODEL_DIR, "ai_settings.json")

# Replay-buffer knobs for the online trainer (see B4 in the plan): a resolved bet is
# eligible as "fresh" until it's been trained on MAX_REPLAY times, after which it can
# still be sampled as part of the older 30% replay slice but no longer prioritized.
# Bumped 300 -> 3000 (12 CPU cores / 14GB RAM, <5% utilized at the old size — see the
# neurobets bank/training conversation) now that backend's scrape trigger no longer
# blocks on this (see backend/main.py's trigger_ai_pipeline) — a slower training pass
# no longer risks eating the next 15s scrape cycle, so there's no reason left to leave
# this hardware idle. With ~150k+ resolved bets backlogged, 2100 fresh/cycle clears it
# in a reasonable number of cycles instead of trickling through 210/cycle.
# Online GRU pass size. 10000 held the engine lock ~9 min/epoch on this CPU VM
# (32 vCPU, ~60% steal); 5000 halves wall time without dropping below MIN_TRAIN_SAMPLES.
TRAIN_BATCH_TOTAL = int(os.getenv("NEURALBET_TRAIN_BATCH_TOTAL", "5000"))
# Share of each class quota drawn from trained_count=0. The rest is replay so the
# GRU cannot memorize a 100% fresh 5k slice in epoch 1. (The fetch helper used to
# ignore this cap and fill the whole quota from fresh.)
TRAIN_FRESH_SHARE = float(os.getenv("NEURALBET_TRAIN_FRESH_SHARE", "0.45"))
# Fraction of the online batch taken from current Brier-gate sports (live stake
# allowlist). Remainder stays on the rest of the universe so dropped sports can
# still recover enough Brier to re-enter the list.
TRAIN_STAKE_SPORT_SHARE = float(os.getenv("NEURALBET_TRAIN_STAKE_SPORT_SHARE", "0.60"))
MAX_REPLAY = 5
VAL_MIN_POOL = 50
VAL_FRACTION = 0.2

# LightGBM is a full batch refit (not incremental) — refitting it from scratch every
# cycle is wasted work once the dataset is large. Refit every Nth cycle instead.
# LGB_TRAIN_LIMIT raised alongside TRAIN_BATCH_TOTAL — GBDT training is cheap even at
# tens of thousands of rows with this small a feature count, and a bigger sample gives
# LightGBM a much more representative picture. 20000 -> 40000: the archive has grown
# past 500k+ resolved bets, so a bigger refit sample stays proportionally representative.
# LGB_REFIT_EVERY_CYCLES 5 -> 20: a full refit on 40k rows every 5 cycles (~75s) was
# wasteful — every 20 cycles keeps it synchronized with the GRU's own new cadence below
# (TRAIN_EVERY_CYCLES) instead of refitting far more often than the data actually turns
# over.
LGB_REFIT_EVERY_CYCLES = int(os.getenv("NEURALBET_LGB_REFIT_EVERY_CYCLES", "20"))
LGB_TRAIN_LIMIT = int(os.getenv("NEURALBET_LGB_TRAIN_LIMIT", "40000"))
LGB_MIN_FRESH_SAMPLES = int(os.getenv("NEURALBET_LGB_MIN_FRESH_SAMPLES", "500"))
LGB_MAX_AGE_HOURS = float(os.getenv("NEURALBET_LGB_MAX_AGE_HOURS", "1"))

# Online-training and ensemble-tuning cadence, in scrape cycles (~15s apart). Both used
# to run every single cycle — for training that meant a gradient step on whatever
# handful of matches (as few as ~80) had just finished, val_loss increasing from the
# very first epoch nearly every pass (pure memorization of that cycle's small batch);
# for tuning it meant blend_weight/decision_threshold visibly whipsawing cycle to cycle
# off the same ~20-40 validation bets. Throttling both means: (a) fresh unresolved-until-
# now bets simply keep accumulating between cycles (trained_count stays 0 — see
# _fetch_training_batch) so the batch that eventually trains is bigger and less noisy,
# and (b) tune_ensemble's own smoothing (_TUNE_SMOOTH_ALPHA) has fewer, more meaningful
# passes to smooth between instead of fighting a new grid-search result every 15s.
# TRAIN_EVERY_CYCLES 10 -> 20 (~2.5min -> ~5min cadence): at TRAIN_BATCH_TOTAL=10000 with
# TRAIN_FRESH_SHARE=0.7 wanting 7000 fresh samples, the real fresh-bet throughput
# (~800/cycle-window observed) meant most of the batch was replay padding, not new
# signal, at the old cadence. Doubling the window roughly doubles how much of the batch
# is genuinely fresh instead of re-chewed history.
# TUNE_EVERY_CYCLES 5 -> 10: EMA smoothing (_TUNE_SMOOTH_ALPHA) already absorbs
# cycle-to-cycle noise in the grid-search target; tuning half as often halves how much
# of that noise it has to smooth away without slowing convergence meaningfully.
TRAIN_EVERY_CYCLES = int(os.getenv("NEURALBET_TRAIN_EVERY_CYCLES", "20"))
TUNE_EVERY_CYCLES = int(os.getenv("NEURALBET_TUNE_EVERY_CYCLES", "10"))
# Catch-up cadence: TRAIN_EVERY_CYCLES=20 exists so newly finished bets can accumulate
# into a full 7000-fresh slice instead of a trickle of replay. That wait is wasted while
# the archive still has a backlog of trained_count=0 rows — those already fill the fresh
# slice (newest first, so live arrivals jump the queue). Train every other cycle
# (train–skip–train, TRAIN_CATCHUP_EVERY_CYCLES=2) until either (a) ≥
# TRAIN_CATCHUP_UNTIL_RATIO of the training-universe archive has been seen at least
# once AND (b) the remaining untrained pool is smaller than one fresh slice. Then
# fall back to 20 so new finishes pile up. Was 5 (four skips between passes) —
# backlog chew was leaving too many unseen rows sitting while we waited.
TRAIN_CATCHUP_EVERY_CYCLES = int(os.getenv("NEURALBET_TRAIN_CATCHUP_EVERY_CYCLES", "2"))
TRAIN_CATCHUP_UNTIL_RATIO = float(os.getenv("NEURALBET_TRAIN_CATCHUP_UNTIL_RATIO", "0.80"))
_COVERAGE_REFRESH_SECONDS = float(os.getenv("NEURALBET_COVERAGE_REFRESH_SECONDS", "60"))
_coverage_cache: dict[str, Any] | None = None
_coverage_loaded_at = 0.0
_coverage_lock = threading.Lock()
_last_catch_up: bool | None = None

# After an admin reset the live weights are random: running the online 10k loop
# (low LR, 50–200 epochs per slice, chart-gate vs the first lucky val_loss) is
# fine-tuning a model that does not exist yet. Cold-start instead walks the
# oldest COLD_START_ARCHIVE_FRACTION of train-universe events for
# COLD_START_EPOCHS sequential passes: each HTTP cycle streams one
# COLD_START_CHUNK slice through a single shuffled mini-batch sweep (no val, no
# early stop, no checkpoint). Validation and the checkpoint gate run only after
# the cold pool epoch completes. Newest ~(1-fraction) events stay
# trained_count=0 for online catch-up. Then the online loop takes over
# (chart-gate on, LEARNING_RATE, random win/loss mix).
COLD_START_EPOCHS = int(os.getenv("NEURALBET_COLD_START_EPOCHS", "2"))
# Fraction of train-universe *events* (oldest by finished_at) used as the
# cold-start pool. Default 0.70 leaves ~30% newest events untrained for catch-up.
COLD_START_ARCHIVE_FRACTION = float(
    os.getenv("NEURALBET_COLD_START_ARCHIVE_FRACTION", "0.70")
)
# One HTTP cycle trains this many archive rows (shuffle in Python). 0 = the entire
# cold pool in one fetch — that OOMs the AI worker during fetchall / exhausts
# the Postgres pool, then Docker restarts and epoch 1/2 begins again.
# 40000 is large enough for from-scratch GRU updates; two outer archive epochs
# still walk the whole pool across cycles via samples_this_epoch offset.
COLD_START_CHUNK = int(os.getenv("NEURALBET_COLD_START_CHUNK", "40000"))
# Deprecated — kept for env compat. Streaming cold-start always does exactly one
# pass per chunk; inner multi-epoch convergence belongs to online training only.
COLD_START_INNER_EPOCHS = int(os.getenv("NEURALBET_COLD_START_INNER_EPOCHS", "1"))
COLD_START_LR = float(os.getenv("NEURALBET_COLD_START_LR", "1e-3"))
COLD_START_LR_DECAY = float(os.getenv("NEURALBET_COLD_START_LR_DECAY", "0.3"))
COLD_START_PATH = os.path.join(MODEL_DIR, "cold_start.json")
# Ordered finished_bets.id list for the frozen cold-start pool. Built once, then
# each chunk is WHERE id = ANY(slice) — OFFSET over JSON trajectories got slower
# every chunk (240k skip ≈ 90s) and sat on a pool connection the whole time.
COLD_START_ROW_IDS_PATH = os.path.join(MODEL_DIR, "cold_start_row_ids.json")
_cold_start_row_ids: list[int] | None = None
VAL_PIN_PATH = os.path.join(MODEL_DIR, "val_pin.json")
VAL_PIN_REFRESH_SECONDS = float(os.getenv("NEURALBET_VAL_PIN_REFRESH_SECONDS", "86400"))
LAST_TUNE_PATH = os.path.join(MODEL_DIR, "last_tune.json")
TEAM_FORM_PATH = os.path.join(MODEL_DIR, "team_form.json")
TEAM_STATS_PATH = os.path.join(MODEL_DIR, "team_stats.json")
TRAINING_HEALTH_SNAPSHOT_PATH = os.path.join(MODEL_DIR, "training_health.json")
_online_pass_count = 0
RESET_PROGRESS_PATH = os.path.join(MODEL_DIR, "reset_progress.json")
_reset_progress_lock = threading.Lock()
_reset_progress: dict[str, Any] = {
    "active": False,
    "step": "idle",
    "label": "",
    "pct": 0,
}


def _cold_start_chunk_label() -> str:
    if COLD_START_CHUNK <= 0:
        return "entire cold pool"
    return f"{COLD_START_CHUNK}-sample chunks"


def _cold_start_archive_fraction() -> float:
    frac = float(COLD_START_ARCHIVE_FRACTION)
    if frac <= 0.0:
        return 0.70
    return min(frac, 1.0)


def _persist_reset_progress(payload: dict[str, Any]) -> None:
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)
        tmp_path = RESET_PROGRESS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, RESET_PROGRESS_PATH)
    except Exception as e:
        logger.error(f"Error persisting reset progress: {e}")


def set_reset_progress(step: str, label: str, pct: int, active: bool = True) -> None:
    with _reset_progress_lock:
        _reset_progress.update({
            "active": active,
            "step": step,
            "label": label,
            "pct": max(0, min(int(pct), 100)),
        })
        payload = dict(_reset_progress)
    _persist_reset_progress(payload)
    logger.info(f"Reset progress {payload['pct']}% — {payload['label']}")


def get_reset_progress() -> dict[str, Any]:
    with _reset_progress_lock:
        return dict(_reset_progress)

# Floor on how many samples (fresh + replay combined — see _fetch_training_batch) a
# training cycle needs before it's allowed to actually run a gradient step. Below this,
# a pass is essentially the "82 samples, best epoch 1/11, val_loss rising from the very
# first epoch" pattern seen in production logs — too few mini-batches for early stopping
# to have anything meaningful to select between; it just memorizes whatever tiny slice
# showed up. Rows fetched on a too-small cycle are deliberately left with trained_count
# untouched (see the skip branch below) so they aren't wasted on a useless step — they
# simply get included again, alongside whatever's newly finished, on the next training
# cycle that clears this floor. 1000 -> 2000: stayed at 10% of TRAIN_BATCH_TOTAL as that
# grew from 5000 to 10000 — 20% is a safer floor so a "too small" pass doesn't still slip
# through at a size barely above the old memorization-prone range.
MIN_TRAIN_SAMPLES = int(os.getenv("NEURALBET_MIN_TRAIN_SAMPLES", "2000"))
# Online pass needs this many never-trained rows. After cold-start the remaining
# trained_count=0 rows are the pinned val holdout — fetching them is impossible,
# so the class-quota filler used to pad with the same replay 10k forever.
MIN_FRESH_SAMPLES = int(os.getenv("NEURALBET_MIN_FRESH_SAMPLES", "500"))
LIVE_QUALITY_GATE = os.getenv("NEURALBET_LIVE_QUALITY_GATE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
LIVE_QUALITY_MIN_BETS = int(os.getenv("NEURALBET_LIVE_QUALITY_MIN_BETS", "40"))

# Live coefficient band / EV / support live in shared/neurobet_filters (MIN_BET_COEFF,
# MAX_BET_COEFF, MIN_BET_EDGE_PCT, MIN_MARKET_SUPPORT) so training, inference, backtest
# and the backend UI cannot drift. Markets this thinly represented still stay IN
# training — they're only excluded from risking live-bankroll money.
MARKET_SUPPORT_REFRESH_SECONDS = float(
    os.getenv("NEURALBET_MARKET_SUPPORT_REFRESH_SECONDS", "300")
)
_market_support: dict[tuple, int] = {}
_market_support_loaded_at = 0.0

AI_LOGS: list[dict[str, Any]] = []
MAX_LOG_ENTRIES = 300
# Shared volume with backend (see docker-compose `./data:/app/data`). Admin "Live Stream"
# used to poll GET /logs over HTTP — that hangs for the whole training pass because this
# process is a single Uvicorn worker doing CPU-bound torch/LightGBM inside the request
# handler (backend/main.py's trigger_ai_pipeline documents the same stall). Backend
# then timed out at 5s and returned `{logs: []}`, which wiped the console. Epoch lines
# are written here from the training thread itself; persisting them to this file lets
# backend serve the feed without talking to a busy worker.
AI_LOGS_PATH = os.path.join(MODEL_DIR, "ai_logs.json")
_logs_lock = threading.Lock()

_cycle_count = 0

# "Is training helping or hurting?" tracking — see get_training_health()'s docstring for
# the full five-signal playbook this feeds. best_epoch == 1-2 on a single pass is
# normal noise; a *streak* of them on batches that already cleared MIN_TRAIN_SAMPLES is
# the real tell (the network is memorizing each fresh batch in one or two epochs instead
# of generalizing — early stopping is doing its job by bailing out fast, but that itself
# is the symptom). Counts only real training passes (skipped-for-too-few-samples cycles
# don't touch this), and resets on reset_neural_network() since a wiped model starting
# over shouldn't inherit its predecessor's bad streak.
LOW_EPOCH_ALERT_THRESHOLD = int(os.getenv("NEURALBET_LOW_EPOCH_ALERT_THRESHOLD", "2"))
LOW_EPOCH_STREAK_ALERT = int(os.getenv("NEURALBET_LOW_EPOCH_STREAK_ALERT", "3"))
_low_epoch_streak = 0

# Consecutive online passes whose weights were rolled back (checkpoint_accepted=False).
# Catches a frozen model that keeps training but never commits — the 0.1657 chart-gate
# reject-storm case where signals A–D all stayed green.
CHECKPOINT_REJECT_STREAK_ALERT = int(
    os.getenv("NEURALBET_CHECKPOINT_REJECT_STREAK_ALERT", "10")
)
# After the reject streak trips, GRU stays off — but the streak is only recomputed after
# a GRU pass, so a hard freeze is a deadlock (catch-up logs "chewing" while untrained
# never shrinks). Probe once every N cycles: accept resets the streak via history;
# reject re-enters the cooldown.
CHECKPOINT_REJECT_PROBE_EVERY_CYCLES = int(
    os.getenv("NEURALBET_CHECKPOINT_REJECT_PROBE_EVERY_CYCLES", "20")
)
_checkpoint_reject_streak = 0


def _persist_ai_logs() -> None:
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)
        tmp_path = AI_LOGS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(AI_LOGS, f, ensure_ascii=False)
        os.replace(tmp_path, AI_LOGS_PATH)
    except Exception as e:
        logger.error(f"Error persisting AI logs: {e}")


def _restore_ai_logs() -> None:
    if not os.path.exists(AI_LOGS_PATH):
        return
    try:
        with open(AI_LOGS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, list):
            with _logs_lock:
                AI_LOGS.clear()
                AI_LOGS.extend(saved[:MAX_LOG_ENTRIES])
    except Exception as e:
        logger.error(f"Error loading AI logs: {e}")


def add_ai_log(category: str, message: str, level: str = "INFO"):
    timestamp_str = now_moscow().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "timestamp": timestamp_str,
        "category": category,
        "level": level,
        "message": message,
    }
    with _logs_lock:
        AI_LOGS.insert(0, entry)
        if len(AI_LOGS) > MAX_LOG_ENTRIES:
            AI_LOGS.pop()
        _persist_ai_logs()
    logger.info(f"[{category}] {message}")


_restore_ai_logs()
add_ai_log(
    "SYSTEM",
    "Standalone AI Microservice initialized with PyTorch & LightGBM.",
)


def _persist_ai_settings() -> None:
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)
        payload = {
            "ai_enabled": bool(AI_SETTINGS["ai_enabled"]),
            "training_enabled": bool(AI_SETTINGS["training_enabled"]),
            "quality_gate_bypass": bool(AI_SETTINGS.get("quality_gate_bypass")),
        }
        tmp_path = AI_SETTINGS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, AI_SETTINGS_PATH)
    except Exception as e:
        logger.error(f"Error persisting AI settings: {e}")


def _restore_ai_settings() -> None:
    if not os.path.exists(AI_SETTINGS_PATH):
        return
    try:
        with open(AI_SETTINGS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for key in (
            "ai_enabled",
            "training_enabled",
            "quality_gate_bypass",
        ):
            if key in saved and saved[key] is not None:
                AI_SETTINGS[key] = bool(saved[key])
        add_ai_log(
            "SYSTEM",
            "AI settings restored from disk: "
            f"inference={'ENABLED' if AI_SETTINGS['ai_enabled'] else 'DISABLED'}, "
            f"training={'ENABLED' if AI_SETTINGS['training_enabled'] else 'DISABLED'}, "
            f"quality_gate_bypass={'ON' if AI_SETTINGS.get('quality_gate_bypass') else 'OFF'}.",
        )
    except Exception as e:
        logger.error(f"Error loading AI settings: {e}")


_restore_ai_settings()
# Survive a failed admin reset without requiring another redeploy — a stuck abort flag
# otherwise makes every cycle return "skipped" with no INFERENCE/TRAINING lines.
_abort_cycle.clear()


def get_ai_settings() -> dict[str, Any]:
    return AI_SETTINGS


def reset_neural_network() -> dict[str, Any]:
    """
    Admin-triggered "Обнулить нейросеть": wipes the live model (fresh random PyTorch
    weights, no LightGBM booster, blend/market weight & decision threshold back to
    defaults — see NeuralBetEnsemble.reset) and clears trained_count on every resolved
    bet, WITHOUT deleting finished_bets/finished_events themselves. Also wipes the
    training/backtest charts (they belong to the old weights) and both bankroll
    accounts back to start_balance, including live_bets and the ledger — otherwise
    the val_loss chart and the 1800 ₽ live bank would still describe the discarded
    model. The archive of resolved matches stays: that's the expensive, slow-to-rebuild
    part. Runs under _engine_lock; _cycle_count goes to 0 so the next inference cycle
    is treated as cycle 1 (training + tune). LightGBM refits only when there is a new
    booster gap, ≥NEURALBET_LGB_MIN_FRESH_SAMPLES new resolved rows, or the last
    accepted booster is older than NEURALBET_LGB_MAX_AGE_HOURS. Starts a
    cold-start walk of the whole archive (from-scratch LR, checkpoint gate off)
    instead of jumping into the online 10k fine-tune loop.
    """
    global _cycle_count, _low_epoch_streak, _checkpoint_reject_streak, _online_pass_count
    from app.neuralbet.backtest import clear_backtest_history

    reset_rows = 0
    try:
        # Wipe any stale "done/100%" left on disk from a prior run so the admin bar
        # cannot jump to success before this reset actually starts.
        set_reset_progress("starting", "Начинаю обнуление…", 1, active=True)
        add_ai_log(
            "SYSTEM",
            "Reset requested — stopping in-flight inference, training and backtest.",
            level="WARNING",
        )
        # A previously failed reset can leave _abort_cycle set and silently block every
        # future predict-and-train pass ("AI cycle skipped — model reset in progress").
        _abort_cycle.clear()
        abort_in_flight_cycle()
        _invalidate_archive_coverage()
        set_reset_progress("waiting_lock", "Жду завершения текущего цикла…", 22)
        with _engine_lock:
            try:
                set_reset_progress("wiping", "Сбрасываю веса модели…", 40)
                ensemble_engine.reset()
                _reset_training_caches()
                set_reset_progress("charts", "Чищу графики обучения и бэктеста…", 52)
                clear_training_history()
                clear_backtest_history()

                set_reset_progress("archive", "Обнуляю trained_count в архиве…", 65)
                f_conn = get_finished_connection()
                f_cursor = f_conn.cursor()
                f_cursor.execute(
                    "UPDATE finished_bets SET trained_count = 0 WHERE trained_count != 0"
                )
                reset_rows = f_cursor.rowcount
                set_reset_progress("bankroll", "Сбрасываю банки и открытые ставки…", 85)
                f_cursor.execute("DELETE FROM live_bets;")
                f_cursor.execute("DELETE FROM bankroll_ledger;")
                f_cursor.execute("""
                    UPDATE bankroll_accounts SET
                        balance = start_balance, peak_balance = start_balance, locked = 0,
                        rounds = 0, bets_placed = 0, wins = 0, losses = 0,
                        total_staked = 0, total_returned = 0, ruin_count = 0, is_ruined = 0,
                        updated_at = now();
                """)
                f_conn.commit()

                _cycle_count = 0
                _low_epoch_streak = 0
                _checkpoint_reject_streak = 0
                set_reset_progress(
                    "cold_start",
                    f"Запускаю cold-start на {_cold_start_archive_fraction():.0%} старейших событий…",
                    94,
                )
                # Must run while f_cursor is still bound to an open connection —
                # release_connection() invalidates the cursor (prod error:
                # "connection pointer is NULL").
                _begin_cold_start(f_cursor)
                release_connection(f_conn)
            finally:
                _release_tracked_conns()

        add_ai_log(
            "SYSTEM",
            f"Neural network reset by admin: PyTorch weights reinitialized, LightGBM booster "
            f"discarded, blend/market weight & decision threshold back to defaults, checkpoint "
            f"files removed, training/backtest charts cleared, live+training bankrolls reset "
            f"to start_balance, trained_count cleared on {reset_rows} resolved bet(s) — "
            f"cold-start will walk the oldest "
            f"{_cold_start_archive_fraction():.0%} of train-universe events in "
            f"{_cold_start_chunk_label()} "
            f"for {COLD_START_EPOCHS} streaming epoch(s) (one pass per chunk, "
            f"checkpoint after each full cold-pool walk) before online fine-tuning resumes.",
            level="WARNING",
        )
        set_reset_progress("done", "Готово", 100, active=False)
        return {"reset_rows": reset_rows}
    except Exception:
        set_reset_progress("error", "Ошибка при обнулении", 0, active=False)
        raise
    finally:
        # Always release — a failed reset used to leave this set and freeze the bot.
        _abort_cycle.clear()


def update_ai_settings(
    ai_enabled: bool | None = None,
    training_enabled: bool | None = None,
    quality_gate_bypass: bool | None = None,
) -> dict[str, Any]:
    changed = False
    if ai_enabled is not None:
        AI_SETTINGS["ai_enabled"] = bool(ai_enabled)
        status_str = "ENABLED" if AI_SETTINGS["ai_enabled"] else "DISABLED"
        add_ai_log("SYSTEM", f"AI Inference toggle changed: {status_str}")
        changed = True
    if training_enabled is not None:
        AI_SETTINGS["training_enabled"] = bool(training_enabled)
        status_str = "ENABLED" if AI_SETTINGS["training_enabled"] else "DISABLED"
        add_ai_log("SYSTEM", f"Online Training toggle changed: {status_str}")
        changed = True
    if quality_gate_bypass is not None:
        AI_SETTINGS["quality_gate_bypass"] = bool(quality_gate_bypass)
        status_str = "ON" if AI_SETTINGS["quality_gate_bypass"] else "OFF"
        add_ai_log(
            "SYSTEM",
            f"Quality gate bypass changed: {status_str}"
            + (
                " — live bets ignore backtest gate."
                if AI_SETTINGS["quality_gate_bypass"]
                else "."
            ),
            level="WARNING" if AI_SETTINGS["quality_gate_bypass"] else "INFO",
        )
        changed = True
    if changed:
        _persist_ai_settings()
    return AI_SETTINGS


def get_ai_logs() -> list[dict[str, Any]]:
    with _logs_lock:
        return list(AI_LOGS)


# Backtest-trend thresholds for get_training_health()'s signals B/C — how many of the
# most recent backtest runs (see backtest.py) to look at. 3 matches the "не улучшается
# 3+ запуска подряд" rule the admin panel's own diagnostic playbook settled on: a single
# bad backtest is noise (the resolved-bet mix each run pulls can shift day to day), a
# streak of 3 is a trend.
TRAINING_HEALTH_BACKTEST_WINDOW = int(
    os.getenv("NEURALBET_TRAINING_HEALTH_BACKTEST_WINDOW", "3")
)
# Signal C (backtest ROI not improving) only counts runs where the current model
# actually placed at least this many bets — see its comment in get_training_health.
TRAINING_HEALTH_MIN_ROI_BETS = int(
    os.getenv("NEURALBET_TRAINING_HEALTH_MIN_ROI_BETS", "100")
)
# Signal D window (val_loss trend) — needs this many recent training passes with a
# val_loss on file before it can fire at all. Split into two halves and their means
# compared (not oldest-vs-newest single points like signal C) because a single
# training pass's val_loss is far noisier than a whole backtest's aggregate ROI: it's
# computed on a ~1000-sample slice with a fresh random trajectory cutoff every pass, so
# two individual passes can easily disagree even when nothing is actually wrong.
# Averaging over a few passes per side smooths that out without needing a long history.
TRAINING_HEALTH_VAL_LOSS_WINDOW = int(
    os.getenv("NEURALBET_TRAINING_HEALTH_VAL_LOSS_WINDOW", "10")
)


def _checkpoint_reject_streak_from_history(train_history: list) -> int:
    """Consecutive rejected online passes, newest first (skips cold-start rows)."""
    streak = 0
    for row in train_history:
        if row.get("cold_start"):
            continue
        if row.get("checkpoint_accepted") is False:
            streak += 1
        else:
            break
    return streak


def _restore_training_streaks() -> None:
    global _checkpoint_reject_streak
    try:
        from app.neuralbet.training_history import get_training_history

        _checkpoint_reject_streak = _checkpoint_reject_streak_from_history(
            get_training_history()
        )
    except Exception as e:
        logger.error(f"Error restoring checkpoint reject streak: {e}")


_restore_training_streaks()


def _fresh_target() -> int:
    return int(TRAIN_BATCH_TOTAL * TRAIN_FRESH_SHARE)


def _invalidate_archive_coverage() -> None:
    global _coverage_cache, _coverage_loaded_at, _archive_win_frac, _archive_win_frac_loaded_at
    _coverage_cache = None
    _coverage_loaded_at = 0.0
    _archive_win_frac = None
    _archive_win_frac_loaded_at = 0.0


def _reset_training_caches() -> None:
    """Reset only on model reset, never after an ordinary trained_count update."""
    global _pinned_val_samples, _pinned_val_loaded_at, _pinned_val_event_ids
    global _online_pass_count
    _pinned_val_samples = None
    _pinned_val_loaded_at = 0.0
    _pinned_val_event_ids = None
    _online_pass_count = 0
    set_team_form_cache(None)
    set_team_stats_cache(None)
    _clear_cold_start_row_ids()
    for path in (VAL_PIN_PATH, TEAM_FORM_PATH, TEAM_STATS_PATH, LAST_TUNE_PATH):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def _default_cold_start() -> dict[str, Any]:
    return {
        "active": False,
        "epoch": 1,
        "epochs_total": COLD_START_EPOCHS,
        "samples_this_epoch": 0,
        "train_pool_size": 0,
        "archive_fraction": _cold_start_archive_fraction(),
        "pool_event_ids": None,
        "pool_finished_at_max": None,
        "pool_event_count": 0,
    }


def _load_cold_start() -> dict[str, Any]:
    state = _default_cold_start()
    if not os.path.exists(COLD_START_PATH):
        return state
    try:
        with open(COLD_START_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            state.update(saved)
    except Exception as e:
        logger.error(f"Error loading cold-start state: {e}")
    return state


def _save_cold_start(state: dict[str, Any]) -> None:
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)
        tmp_path = COLD_START_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, COLD_START_PATH)
    except Exception as e:
        logger.error(f"Error persisting cold-start state: {e}")


def _select_cold_start_pool_event_ids(
    f_cursor, val_event_ids: set | None,
) -> tuple[list[int], Any, int]:
    """Oldest floor(fraction * n_events) train-universe events by finished_at.

    Returns (event_ids, max_finished_at cutoff, n_events_in_universe).
    """
    uni, where_val, params = _universe_filter(val_event_ids)
    f_cursor.execute(
        f"""
        SELECT event_id, ev_finished_at FROM (
            SELECT h.event_id, MAX(h.finished_at) AS ev_finished_at
              FROM finished_bets h
              JOIN finished_events f ON h.event_id = f.event_id
             WHERE h.is_win IS NOT NULL
             {uni} {where_val}
             GROUP BY h.event_id
        ) t
        ORDER BY ev_finished_at ASC NULLS LAST, event_id ASC
        """,
        list(params),
    )
    rows = f_cursor.fetchall()
    n_events = len(rows)
    frac = _cold_start_archive_fraction()
    n_pool = n_events if frac >= 1.0 else int(math.floor(frac * n_events))
    if n_events > 0 and n_pool <= 0:
        n_pool = 1
    pool = rows[:n_pool]
    event_ids = [int(r["event_id"]) for r in pool]
    cutoff = pool[-1]["ev_finished_at"] if pool else None
    return event_ids, cutoff, n_events


def _ensure_cold_start_pool(
    f_cursor, state: dict[str, Any], val_event_ids: set | None,
) -> dict[str, Any]:
    """Populate pool_event_ids / train_pool_size once when cold-start begins."""
    if not state.get("active"):
        return state
    existing = state.get("pool_event_ids")
    if isinstance(existing, list) and existing:
        if not state.get("train_pool_size"):
            state["train_pool_size"] = _count_train_pool(
                f_cursor, val_event_ids, event_ids=existing,
            )
            _save_cold_start(state)
        return state

    event_ids, cutoff, n_events = _select_cold_start_pool_event_ids(
        f_cursor, val_event_ids,
    )
    frac = _cold_start_archive_fraction()
    state["pool_event_ids"] = event_ids
    state["pool_finished_at_max"] = cutoff
    state["pool_event_count"] = len(event_ids)
    state["archive_fraction"] = frac
    state["train_pool_size"] = _count_train_pool(
        f_cursor, val_event_ids, event_ids=event_ids,
    )
    _save_cold_start(state)
    _clear_cold_start_row_ids()
    add_ai_log(
        "TRAINING",
        f"Cold-start pool: oldest {len(event_ids)}/{n_events} events "
        f"(fraction={frac:.2f}, {state['train_pool_size']} bet rows, "
        f"cutoff finished_at={cutoff!r}). Newest "
        f"{max(n_events - len(event_ids), 0)} events stay untrained for catch-up.",
    )
    return state


def _clear_cold_start_row_ids() -> None:
    global _cold_start_row_ids
    _cold_start_row_ids = None
    try:
        if os.path.exists(COLD_START_ROW_IDS_PATH):
            os.remove(COLD_START_ROW_IDS_PATH)
    except Exception:
        pass


def _ensure_cold_start_row_ids(
    f_cursor,
    val_event_ids: set | None,
    pool_event_ids: list | None,
) -> list[int]:
    """Stable ordered finished_bets.id list for the frozen cold-start pool.

    Built once (ids only, no trajectory JSON), then each chunk is a PK lookup.
    """
    global _cold_start_row_ids
    if _cold_start_row_ids:
        return _cold_start_row_ids
    if os.path.exists(COLD_START_ROW_IDS_PATH):
        try:
            with open(COLD_START_ROW_IDS_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list) and loaded:
                _cold_start_row_ids = [int(x) for x in loaded]
                return _cold_start_row_ids
        except Exception as e:
            logger.error(f"Error loading cold-start row ids: {e}")

    if not pool_event_ids:
        _cold_start_row_ids = []
        return _cold_start_row_ids

    uni, where_val, params = _universe_filter(val_event_ids)
    add_ai_log(
        "TRAINING",
        f"Building cold-start row index ({len(pool_event_ids)} events) — "
        "one-time sort, later chunks use PK lookups.",
    )
    f_cursor.execute(
        f"""
        SELECT h.id
        FROM finished_bets h
        JOIN finished_events f ON h.event_id = f.event_id
        WHERE h.is_win IS NOT NULL
        {uni} {where_val}
        AND h.event_id = ANY(%s)
        ORDER BY h.finished_at ASC, h.event_id, h.factor_id,
                 h.parameter, h.market_prefix, h.id
        """,
        list(params) + [list(pool_event_ids)],
    )
    ids = [int(r["id"]) for r in f_cursor.fetchall() if r and r.get("id") is not None]
    _cold_start_row_ids = ids
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)
        tmp_path = COLD_START_ROW_IDS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(ids, f)
        os.replace(tmp_path, COLD_START_ROW_IDS_PATH)
    except Exception as e:
        logger.error(f"Error persisting cold-start row ids: {e}")
    add_ai_log("TRAINING", f"Cold-start row index ready — {len(ids)} bet rows.")
    return ids


def _begin_cold_start(f_cursor=None, val_event_ids: set | None = None) -> dict[str, Any]:
    _clear_cold_start_row_ids()
    state = {
        "active": True,
        "epoch": 1,
        "epochs_total": COLD_START_EPOCHS,
        "samples_this_epoch": 0,
        "train_pool_size": 0,
        "archive_fraction": _cold_start_archive_fraction(),
        "pool_event_ids": None,
        "pool_finished_at_max": None,
        "pool_event_count": 0,
    }
    if f_cursor is not None:
        state = _ensure_cold_start_pool(f_cursor, state, val_event_ids)
    else:
        _save_cold_start(state)
    _invalidate_archive_coverage()
    return state


def _finish_cold_start(state: dict[str, Any]) -> dict[str, Any]:
    state["active"] = False
    _save_cold_start(state)
    _clear_cold_start_row_ids()
    _invalidate_archive_coverage()
    add_ai_log(
        "TRAINING",
        "Cold-start finished — switching to online 10k passes (chart-gate on, "
        f"lr={os.getenv('NEURALBET_LEARNING_RATE', '5e-5')}, random win/loss mix). "
        "Newest ~"
        f"{max(0, int(round((1.0 - float(state.get('archive_fraction') or 0.7)) * 100)))}"
        "% of events remain trained_count=0 for catch-up.",
    )
    return state


def _cold_start_lr(epoch: int) -> float:
    return COLD_START_LR * (COLD_START_LR_DECAY ** max(epoch - 1, 0))


def _advance_cold_start(state: dict[str, Any], samples_used: int) -> dict[str, Any]:
    """Count this chunk toward the current cold-pool epoch; roll to the next
    epoch or deactivate once the cold pool has been seen once."""
    if not state.get("active"):
        return state
    state["samples_this_epoch"] = int(state.get("samples_this_epoch") or 0) + samples_used
    pool = int(state.get("train_pool_size") or 0)
    epoch = int(state.get("epoch") or 1)
    epochs_total = int(state.get("epochs_total") or COLD_START_EPOCHS)
    if pool > 0 and state["samples_this_epoch"] >= pool:
        if epoch >= epochs_total:
            return _finish_cold_start(state)
        state["epoch"] = epoch + 1
        state["samples_this_epoch"] = 0
        add_ai_log(
            "TRAINING",
            f"Cold-start epoch {epoch}/{epochs_total} done ({pool} samples) — "
            f"starting epoch {state['epoch']} at lr={_cold_start_lr(state['epoch']):.4g}.",
        )
    _save_cold_start(state)
    _invalidate_archive_coverage()
    return state


def _holdout_event_ids() -> set | None:
    """Pinned validation events — excluded from train-pool coverage and catch-up."""
    if _pinned_val_event_ids:
        return _pinned_val_event_ids
    if not os.path.exists(VAL_PIN_PATH):
        return None
    try:
        with open(VAL_PIN_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)
        ids = set(meta.get("event_ids") or [])
        return ids or None
    except Exception:
        return None


def get_archive_training_coverage(force: bool = False) -> dict[str, Any]:
    """Share of the *train-pool* archive that has trained_count > 0.

    Pinned validation events are excluded: they are never marked trained, so
    counting them as "unseen" kept catch-up on forever after cold-start.
    Cached for _COVERAGE_REFRESH_SECONDS.
    """
    global _coverage_cache, _coverage_loaded_at, _last_catch_up
    now = time.monotonic()
    if (
        not force
        and _coverage_cache is not None
        and (now - _coverage_loaded_at) < _COVERAGE_REFRESH_SECONDS
    ):
        return _coverage_cache

    # Single-flight: waiters sit on the lock (no extra DB checkouts) until the
    # in-flight COUNT fills the cache. A cache-miss stampede used to exhaust
    # the AI-service pool and kill every inference cycle.
    with _coverage_lock:
        now = time.monotonic()
        if (
            not force
            and _coverage_cache is not None
            and (now - _coverage_loaded_at) < _COVERAGE_REFRESH_SECONDS
        ):
            return _coverage_cache

        untrained = trained = total = 0
        holdout: set | None = None
        f_conn = None
        try:
            f_conn = get_finished_connection()
            f_cursor = f_conn.cursor()
            sports, factors = universe_sql_params()
            holdout = _holdout_event_ids()
            holdout_sql = "AND h.event_id != ALL(%s)" if holdout else ""
            params: list[Any] = [sports, factors]
            if holdout:
                params.append(list(holdout))
            f_cursor.execute(
                f"""
                SELECT
                  COUNT(*) FILTER (WHERE h.trained_count = 0) AS untrained,
                  COUNT(*) FILTER (WHERE h.trained_count > 0) AS trained,
                  COUNT(*) AS total
                FROM finished_bets h
                JOIN finished_events f ON h.event_id = f.event_id
                WHERE h.is_win IS NOT NULL
                {universe_sql("f", "h")}
                {holdout_sql}
                """,
                params,
            )
            row = f_cursor.fetchone()
            if row:
                untrained = int(row["untrained"] or 0)
                trained = int(row["trained"] or 0)
                total = int(row["total"] or 0)
        except Exception as e:
            logger.error(f"Error querying archive training coverage: {e}")
            if _coverage_cache is not None:
                return _coverage_cache
        finally:
            if f_conn is not None:
                release_connection(f_conn)

        trained_ratio = (trained / total) if total else 0.0
        fresh_n = _fresh_target()
        cold_start = _load_cold_start()
        catch_up = total > 0 and (
            trained_ratio < TRAIN_CATCHUP_UNTIL_RATIO or untrained >= fresh_n
        )
        if cold_start.get("active"):
            catch_up = True
            every = 1
        else:
            every = TRAIN_CATCHUP_EVERY_CYCLES if catch_up else TRAIN_EVERY_CYCLES
        payload = {
            "untrained": untrained,
            "trained": trained,
            "total": total,
            "trained_ratio": round(trained_ratio, 4),
            "catch_up": catch_up,
            "train_every_cycles": every,
            "fresh_target": fresh_n,
            "val_holdout_events": len(holdout) if holdout else 0,
            "cold_start": {
                "active": bool(cold_start.get("active")),
                "epoch": int(cold_start.get("epoch") or 1),
                "epochs_total": int(cold_start.get("epochs_total") or COLD_START_EPOCHS),
                "samples_this_epoch": int(cold_start.get("samples_this_epoch") or 0),
                "train_pool_size": int(cold_start.get("train_pool_size") or 0),
                "chunk": COLD_START_CHUNK,
            },
        }
        if _last_catch_up is None or catch_up != _last_catch_up:
            add_ai_log(
                "TRAINING",
                (
                    f"Catch-up training {'ON' if catch_up else 'OFF'} — train pool "
                    f"{trained_ratio:.0%} trained ({trained}/{total}), {untrained} unseen"
                    + (
                        f", {payload['val_holdout_events']} val events held out"
                        if payload["val_holdout_events"]
                        else ""
                    )
                    + f", cadence every {every} cycles."
                ),
            )
        _last_catch_up = catch_up
        _coverage_cache = payload
        _coverage_loaded_at = now
        return payload


def get_live_quality_gate() -> dict[str, Any]:
    """Re-evaluate the live-bet quality gate from the latest backtest on disk.

    Same policy `_live_quality_skip_reason` uses (walk-forward preferred, age check
    on). Exposed via training-health so the admin panel can show the gate without
    waiting for an in-page backtest run.
    """
    from app.neuralbet.backtest import get_backtest_history, get_latest_backtest
    from app.neuralbet.quality_gate import evaluate_quality_gate

    bypass = bool(AI_SETTINGS.get("quality_gate_bypass"))
    latest = get_latest_backtest()
    if not latest:
        return {
            "enabled": True,
            "pass": False,
            "eval_slice": None,
            "reasons": ["no backtest yet"],
            "metrics": {},
            "bypass": bypass,
        }
    gate = evaluate_quality_gate(
        latest, history=get_backtest_history(), check_age=True,
    )
    out = dict(gate)
    out["bypass"] = bypass
    return out


def _live_quality_skip_reason() -> str | None:
    """Block new virtual live bets until the latest backtest shows an edge on OOS."""
    if AI_SETTINGS.get("quality_gate_bypass"):
        return None
    gate = get_live_quality_gate()
    if not gate.get("enabled"):
        return None
    reasons = gate.get("reasons") or []
    return "; ".join(reasons) if reasons else None


def get_training_health() -> dict[str, Any]:
    """
    Traffic-light read on whether online training is currently helping or hurting,
    combining five signals from the admin's own diagnostic playbook:
      A) low_epoch_streak — LOW_EPOCH_STREAK_ALERT+ consecutive real training passes
         (on batches that already cleared MIN_TRAIN_SAMPLES) where best_epoch was
         <= LOW_EPOCH_ALERT_THRESHOLD: the network memorized each fresh batch in 1-2
         epochs instead of generalizing.
      B) backtest_brier_not_beating_market — the current model's Brier score stayed >=
         the bare bookmaker-implied Brier (val_brier_base) across the last
         TRAINING_HEALTH_BACKTEST_WINDOW backtest runs: the model isn't adding
         information over just trusting the odds, on held-out history.
      C) backtest_roi_not_improving — the current model's backtest ROI hasn't improved
         between the oldest and newest run in that same window.
      D) val_loss_trending_up — the average validation decision-loss *attempted this
         pass* (val_loss_attempted, not the chart's carried-forward checkpoint value)
         over the most recent half of the last TRAINING_HEALTH_VAL_LOSS_WINDOW training
         passes is higher than the average over the older half: a slower, more gradual
         drift than signal A (which only catches a single pass memorizing its batch
         outright) — this is the earliest available signal of all five, since a
         training pass fires far more often than a backtest, but also the noisiest
         per-pass, hence the within-window averaging instead of a point-to-point
         comparison. Rejected passes still count: using the frozen chart val_loss
         would hide a reject-storm behind a flat "ok" line.
      E) checkpoint_reject_streak — CHECKPOINT_REJECT_STREAK_ALERT+ consecutive online
         passes where checkpoint_accepted is False: GRU is paused (with a periodic
         probe every CHECKPOINT_REJECT_PROBE_EVERY_CYCLES) so a reject-storm does not
         burn CPU every catch-up cycle, and catch-up is flagged blocked until a probe
         commits — e.g. an incoming gate that no fresh pass can beat.
    One active signal is "presmotret'sya" (warning); a majority (3 of 5) is "definite
    stop" (danger) — see run_neuralbet_inference_and_training's docstring history / the
    admin panel's status block, which renders this directly. Needs at least
    TRAINING_HEALTH_BACKTEST_WINDOW backtest runs on file for B/C to activate at all,
    and TRAINING_HEALTH_VAL_LOSS_WINDOW training passes with a val_loss for D — with
    fewer of either, only signal A (which needs neither) can fire. Signal E uses the
    live in-process streak counter (also derivable from training_history).

    Returns status "disabled" (not ok/warning/danger) whenever the admin's own
    training_enabled toggle is off: with no gradient steps running, none of the five
    signals describe anything currently happening — they'd just be stale readings from
    whenever training last ran, and showing a green/red verdict on that would imply an
    ongoing process that isn't there. Signals are still reported (as their last-known
    values) so the panel can show the numbers, just not color-coded as if they were live.
    """
    from app.neuralbet.backtest import get_backtest_history

    if not AI_SETTINGS["training_enabled"]:
        from app.neuralbet.training_history import get_training_history

        disabled_coverage = get_archive_training_coverage()
        disabled_reject_streak = _checkpoint_reject_streak_from_history(
            get_training_history()
        )
        return {
            "status": "disabled",
            "archive_coverage": disabled_coverage,
            "catch_up_blocked_by_checkpoint_streak": bool(
                disabled_coverage.get("catch_up")
                and disabled_reject_streak >= CHECKPOINT_REJECT_STREAK_ALERT
            ),
            "quality_gate": get_live_quality_gate(),
            "signals": {
                "low_epoch_streak": {
                    "active": False,
                    "streak": _low_epoch_streak,
                    "threshold": LOW_EPOCH_STREAK_ALERT,
                },
                "backtest_brier_not_beating_market": {
                    "active": False,
                    "runs_checked": 0,
                    "runs_needed": TRAINING_HEALTH_BACKTEST_WINDOW,
                },
                "backtest_roi_not_improving": {
                    "active": False,
                    "runs_checked": 0,
                    "runs_needed": TRAINING_HEALTH_BACKTEST_WINDOW,
                },
                "val_loss_trending_up": {
                    "active": False,
                    "runs_checked": 0,
                    "runs_needed": TRAINING_HEALTH_VAL_LOSS_WINDOW,
                },
                "checkpoint_reject_streak": {
                    "active": False,
                    "streak": disabled_reject_streak,
                    "threshold": CHECKPOINT_REJECT_STREAK_ALERT,
                    "probe_every_cycles": CHECKPOINT_REJECT_PROBE_EVERY_CYCLES,
                },
            },
        }

    from app.neuralbet.training_history import get_training_history

    signal_a = _low_epoch_streak >= LOW_EPOCH_STREAK_ALERT

    history = get_backtest_history()  # newest first
    recent = history[:TRAINING_HEALTH_BACKTEST_WINDOW]
    have_enough_backtests = len(recent) >= TRAINING_HEALTH_BACKTEST_WINDOW

    signal_b = False
    signal_c = False
    if have_enough_backtests:
        briers = [
            ((r.get("overall") or {}).get("current") or {}).get("brier") for r in recent
        ]
        market_briers = [(r.get("overall") or {}).get("market_brier") for r in recent]
        if all(b is not None for b in briers) and all(
            m is not None for m in market_briers
        ):
            signal_b = all(b >= m for b, m in zip(briers, market_briers))

        rois = [
            ((r.get("overall") or {}).get("current") or {}).get("roi_pct")
            for r in recent
        ]
        bet_counts = [
            ((r.get("overall") or {}).get("current") or {}).get("bets") or 0
            for r in recent
        ]
        # ROI over a few dozen bets is dominated by variance, not skill — after the
        # live-betting gates (coeff cap, EV floor, market support, one-per-event)
        # tightened the funnel, backtest runs routinely place ~30 bets out of 15k+
        # evaluated outcomes, where a single 2.0-coefficient win swings ROI by ±7pp.
        # Comparing two such numbers and calling the difference a "trend" would make
        # this signal flap randomly in both directions, so it stays silent until every
        # run in the window has enough bets for its ROI to mean anything.
        if all(v is not None for v in rois) and all(
            b >= TRAINING_HEALTH_MIN_ROI_BETS for b in bet_counts
        ):
            # recent[0] is newest, recent[-1] is oldest in this window.
            signal_c = rois[0] <= rois[-1]

    train_history = get_training_history()  # newest first
    # Rejected passes copy the previous checkpoint's val_loss onto the admin
    # chart (so 0.1497 stays 0.1497). Health must look at this pass's actual
    # trained val_loss — val_loss_attempted — otherwise a reject-storm reports
    # "ok". Fall back to incoming (live weights on this val split) then to the
    # recorded chart value for rows from before those fields existed.
    def _health_val_loss(row: dict) -> float | None:
        attempted = row.get("val_loss_attempted")
        if attempted is not None:
            return attempted
        if row.get("checkpoint_accepted") is False:
            incoming = row.get("val_loss_incoming")
            return incoming if incoming is not None else row.get("val_loss")
        return row.get("val_loss")

    val_losses = [
        v for v in (_health_val_loss(r) for r in train_history) if v is not None
    ][:TRAINING_HEALTH_VAL_LOSS_WINDOW]
    have_enough_val_passes = len(val_losses) >= TRAINING_HEALTH_VAL_LOSS_WINDOW

    signal_d = False
    if have_enough_val_passes:
        half = TRAINING_HEALTH_VAL_LOSS_WINDOW // 2
        newer_avg = sum(val_losses[:half]) / half
        older_avg = sum(val_losses[half:]) / (len(val_losses) - half)
        signal_d = newer_avg > older_avg

    reject_streak = _checkpoint_reject_streak_from_history(train_history)
    signal_e = reject_streak >= CHECKPOINT_REJECT_STREAK_ALERT
    coverage = get_archive_training_coverage()

    active = sum([signal_a, signal_b, signal_c, signal_d, signal_e])
    status = "danger" if active >= 3 else "warning" if active >= 1 else "ok"

    health = {
        "status": status,
        "archive_coverage": coverage,
        "catch_up_blocked_by_checkpoint_streak": bool(
            coverage.get("catch_up") and signal_e
        ),
        "quality_gate": get_live_quality_gate(),
        "signals": {
            "low_epoch_streak": {
                "active": signal_a,
                "streak": _low_epoch_streak,
                "threshold": LOW_EPOCH_STREAK_ALERT,
            },
            "backtest_brier_not_beating_market": {
                "active": signal_b,
                "runs_checked": len(recent),
                "runs_needed": TRAINING_HEALTH_BACKTEST_WINDOW,
            },
            "backtest_roi_not_improving": {
                "active": signal_c,
                "runs_checked": len(recent),
                "runs_needed": TRAINING_HEALTH_BACKTEST_WINDOW,
            },
            "val_loss_trending_up": {
                "active": signal_d,
                "runs_checked": len(val_losses),
                "runs_needed": TRAINING_HEALTH_VAL_LOSS_WINDOW,
            },
            "checkpoint_reject_streak": {
                "active": signal_e,
                "streak": reject_streak,
                "threshold": CHECKPOINT_REJECT_STREAK_ALERT,
                "probe_every_cycles": CHECKPOINT_REJECT_PROBE_EVERY_CYCLES,
            },
        },
    }
    _persist_training_health_snapshot(health)
    return health


def _get_val_event_ids(f_cursor) -> set | None:
    """
    Returns the set of event_ids held out for validation: the most-recently-finished
    ~VAL_FRACTION of *events* (not bet-rows). Splitting by row count instead of event
    would skew the held-out set towards whichever sports happen to resolve the most
    markets per match (a football match settles a dozen+ markets, a 1-on-1 tennis one
    settles a handful) — event-level counting keeps the split representative, and
    membership-based filtering (rather than a single finished_at threshold) guarantees
    every bet from a held-out match lands on the val side together, so no match can ever
    straddle the train/val boundary — see plan B6. Returns None if there isn't enough
    resolved history yet to bother holding anything out.
    """
    sports, factors = universe_sql_params()
    f_cursor.execute(
        f"""
        SELECT COUNT(DISTINCT h.event_id) AS c
        FROM finished_bets h
        JOIN finished_events f ON h.event_id = f.event_id
        WHERE h.is_win IS NOT NULL
        {universe_sql("f", "h")}
        """
    , (sports, factors))
    total_events = f_cursor.fetchone()["c"]
    if total_events < VAL_MIN_POOL:
        return None
    val_event_count = max(int(total_events * VAL_FRACTION), 10)
    f_cursor.execute(
        f"""
        SELECT event_id FROM (
            SELECT h.event_id, MAX(h.finished_at) AS ev_finished_at
              FROM finished_bets h
              JOIN finished_events f ON h.event_id = f.event_id
             WHERE h.is_win IS NOT NULL
             {universe_sql("f", "h")}
             GROUP BY h.event_id
        ) t
        ORDER BY ev_finished_at DESC
        LIMIT %s
    """,
        (sports, factors, val_event_count),
    )
    return {r["event_id"] for r in f_cursor.fetchall()}


_row_to_sample = row_to_sample


_TRAIN_ROW_SELECT = """
        SELECT h.event_id, h.factor_id, h.parameter, h.market_prefix, h.is_win,
               h.odds_seq_json, h.score_seq_json, h.score_sum_seq_json,
               h.ts_seq_json, h.timer_seq_json, h.overround_seq_json,
               h.score_diff_at_bet, h.finished_at, h.overround_close, h.trained_count,
               f.sport_path, f.team_1, f.team_2
        FROM finished_bets h
        JOIN finished_events f ON h.event_id = f.event_id
        WHERE h.is_win IS NOT NULL
"""


def _universe_filter(val_event_ids: set | None) -> tuple[str, str, list]:
    sports, factors = universe_sql_params()
    uni = universe_sql("f", "h")
    if val_event_ids:
        return uni, "AND h.event_id != ALL(%s)", [sports, factors, list(val_event_ids)]
    return uni, "", [sports, factors]


def _rows_to_train_samples(rows) -> list[dict[str, Any]]:
    return [
        s
        for s in (_row_to_sample(r) for r in rows)
        if in_train_universe(s["sport_path"], s["factor_id"], s.get("parameter"))
    ]


def _count_train_pool(
    f_cursor,
    val_event_ids: set | None,
    untrained_only: bool = False,
    event_ids: list | None = None,
) -> int:
    uni, where_val, params = _universe_filter(val_event_ids)
    extra = "AND h.trained_count = 0" if untrained_only else ""
    pool_clause = ""
    query_params = list(params)
    if event_ids is not None:
        pool_clause = "AND h.event_id = ANY(%s)"
        query_params.append(list(event_ids))
    f_cursor.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM finished_bets h
        JOIN finished_events f ON h.event_id = f.event_id
        WHERE h.is_win IS NOT NULL {extra} {uni} {where_val} {pool_clause}
        """,
        query_params,
    )
    row = f_cursor.fetchone()
    return int(row["c"] or 0) if row else 0


# Floor/ceiling on the per-pass win share. A hard 0/N–N/0 draw (the original
# "don't latch onto 50/50") produced batches like 223/9777: the GRU memorized the
# majority class in 1–2 epochs, the checkpoint gate rejected the pass, and
# training spun in place. 20–80% still moves the prior around each cycle without
# collapsing a 10k batch into a single-class prior.
TRAIN_CLASS_QUOTA_MIN_FRAC = float(os.getenv("NEURALBET_TRAIN_CLASS_QUOTA_MIN_FRAC", "0.20"))


def _draw_class_quota(total: int, win_frac: float) -> tuple[int, int]:
    """Win/loss counts pinned to the archive's empirical win rate (same idea as
    the val split) but clamped to [TRAIN_CLASS_QUOTA_MIN_FRAC, 1-frac] so the
    batch never collapses to a single class."""
    if total <= 0:
        return 0, 0
    lo = int(round(total * TRAIN_CLASS_QUOTA_MIN_FRAC))
    hi = total - lo
    if lo > hi:
        lo, hi = hi, lo
    n_win = int(round(total * max(0.0, min(1.0, win_frac))))
    n_win = max(lo, min(hi, n_win))
    return n_win, total - n_win


def _fetch_class_rows(
    f_cursor,
    val_event_ids: set | None,
    is_win: int,
    n: int,
    seen: set,
    *,
    sport_in: list[str] | None = None,
    sport_not_in: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fill up to `n` rows of one class: untrained newest first, then replay, then
    any remaining of that class. Dedupes against `seen` (mutated)."""
    if n <= 0:
        return []
    uni, where_val, base_params = _universe_filter(val_event_ids)
    win_clause = "AND h.is_win = %s"
    sport_sql = ""
    sport_params: list = []
    sport_expr = "LOWER(TRIM(SPLIT_PART(f.sport_path, '/', 1)))"
    if sport_in:
        sport_sql = f"AND {sport_expr} = ANY(%s)"
        sport_params = [list(sport_in)]
    elif sport_not_in:
        sport_sql = f"AND NOT ({sport_expr} = ANY(%s))"
        sport_params = [list(sport_not_in)]
    out: list[dict[str, Any]] = []

    def _take(order_sql: str, extra_where: str, extra_params: list, limit: int) -> None:
        nonlocal out
        cap = min(n, len(out) + max(0, limit))
        need = cap - len(out)
        if need <= 0 or limit <= 0:
            return
        fetch_n = max(need * 2, need + 32)
        f_cursor.execute(
            f"""
            {_TRAIN_ROW_SELECT}
            {uni} {where_val} {sport_sql} {win_clause} {extra_where}
            {order_sql}
            LIMIT %s
            """,
            base_params + sport_params + [is_win] + extra_params + [fetch_n],
        )
        for s in _rows_to_train_samples(f_cursor.fetchall()):
            key = s["_key"]
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
            if len(out) >= cap:
                return

    fresh_n = int(n * TRAIN_FRESH_SHARE)
    if fresh_n <= 0 and n > 0:
        fresh_n = n
    replay_n = n - fresh_n
    _take("ORDER BY h.finished_at DESC", "AND h.trained_count = 0", [], fresh_n)
    _take(
        "ORDER BY RANDOM()",
        "AND h.trained_count BETWEEN 1 AND %s",
        [MAX_REPLAY - 1],
        replay_n if replay_n > 0 else (n - len(out)),
    )
    if len(out) < n:
        _take("ORDER BY RANDOM()", "", [], n - len(out))
    return out


def _fetch_training_batch(
    f_cursor, val_event_ids: set | None
) -> tuple[list[dict[str, Any]], list[tuple], dict[str, int]]:
    """
    Builds this cycle's training batch: TRAIN_FRESH_SHARE never-trained bets
    (freshest first) + replay so the GRU cannot memorize a 100% fresh slice in
    epoch 1. Prefer TRAIN_STAKE_SPORT_SHARE from the current Brier-gate sports.
    Win/loss counts follow the archive empirical win rate (_get_archive_win_frac).
    Excludes every bet belonging to a held-out validation event.
    Returns (rows, key_list, mix) where mix is the drawn quota plus what was actually
    fetched.
    """
    win_frac = _get_archive_win_frac(f_cursor)
    n_win, n_loss = _draw_class_quota(TRAIN_BATCH_TOTAL, win_frac)
    seen: set = set()
    stake_sports = effective_live_stake_sports()
    share = TRAIN_STAKE_SPORT_SHARE
    if stake_sports and 0.0 < share < 1.0:
        stake_list = sorted(stake_sports)
        n_sw = int(round(n_win * share))
        n_sl = int(round(n_loss * share))
        wins = _fetch_class_rows(
            f_cursor, val_event_ids, 1, n_sw, seen, sport_in=stake_list,
        )
        losses = _fetch_class_rows(
            f_cursor, val_event_ids, 0, n_sl, seen, sport_in=stake_list,
        )
        wins.extend(_fetch_class_rows(
            f_cursor, val_event_ids, 1, n_win - len(wins), seen, sport_not_in=stake_list,
        ))
        losses.extend(_fetch_class_rows(
            f_cursor, val_event_ids, 0, n_loss - len(losses), seen, sport_not_in=stake_list,
        ))
        if len(wins) < n_win:
            wins.extend(_fetch_class_rows(
                f_cursor, val_event_ids, 1, n_win - len(wins), seen,
            ))
        if len(losses) < n_loss:
            losses.extend(_fetch_class_rows(
                f_cursor, val_event_ids, 0, n_loss - len(losses), seen,
            ))
    else:
        wins = _fetch_class_rows(f_cursor, val_event_ids, 1, n_win, seen)
        losses = _fetch_class_rows(f_cursor, val_event_ids, 0, n_loss, seen)
    samples = wins + losses
    random.shuffle(samples)
    keys = [s["_key"] for s in samples]
    stake_set = set(stake_sports or [])
    mix = {
        "target_win": n_win,
        "target_loss": n_loss,
        "got_win": len(wins),
        "got_loss": len(losses),
        "got_fresh": sum(1 for s in samples if int(s.get("trained_count") or 0) == 0),
        "got_replay": sum(1 for s in samples if int(s.get("trained_count") or 0) > 0),
        "got_stake_sport": sum(
            1 for s in samples
            if (s.get("sport_path") or "").split("/")[0].strip().lower() in stake_set
        ) if stake_set else len(samples),
    }
    return samples, keys, mix


def _fetch_cold_start_batch(
    f_cursor,
    val_event_ids: set | None,
    epoch: int,
    offset: int = 0,
    pool_event_ids: list | None = None,
) -> tuple[list[dict[str, Any]], list[tuple]]:
    """Sequential streaming slice of the cold-start event pool (one epoch = full pass)."""
    uni, where_val, params = _universe_filter(val_event_ids)
    unlimited = COLD_START_CHUNK <= 0
    ids = _ensure_cold_start_row_ids(f_cursor, val_event_ids, pool_event_ids)
    if not ids:
        return [], []
    if unlimited:
        slice_ids = ids[offset:]
    else:
        slice_ids = ids[offset:offset + COLD_START_CHUNK]
    if not slice_ids:
        return [], []
    f_cursor.execute(
        f"""
        {_TRAIN_ROW_SELECT}
        {uni} {where_val}
        AND h.id = ANY(%s)
        """,
        list(params) + [slice_ids],
    )
    samples = _rows_to_train_samples(f_cursor.fetchall())
    rng = random.Random(VAL_CUTOFF_SEED + epoch * 1_000_003 + offset)
    rng.shuffle(samples)
    keys = [s["_key"] for s in samples]
    return samples, keys


# 400 -> 1000 -> 2000: everything downstream of this slice — per-epoch early-stopping
# selection, the tuner's Brier grid, the decision-threshold ROI sweep — was reading a
# view small enough that batch-to-batch noise in which bets landed in it moved the
# "best" epoch and threshold around. Bumped alongside TRAIN_BATCH_TOTAL each time
# (bigger training passes deserve a bigger yardstick); the val *pool* (VAL_FRACTION of
# events) is already far bigger than this cap, so raising it just uses more of what's
# already held out rather than needing more data.
VAL_BATCH_LIMIT = int(os.getenv("NEURALBET_VAL_BATCH_LIMIT", "2000"))
# Val split uses the archive's empirical win rate (cached) instead of whatever mix
# happens to land in the first LIMIT rows — val_loss is not comparable across passes
# when hit-rate swings 25%→77% batch-to-batch. Clamped to the same 20–80% band as
# training quotas so tiny sport skews cannot collapse the split.
ARCHIVE_WIN_FRAC_REFRESH_SECONDS = float(
    os.getenv("NEURALBET_ARCHIVE_WIN_FRAC_REFRESH_SECONDS", "300")
)
_archive_win_frac: float | None = None
_archive_win_frac_loaded_at = 0.0


def _get_archive_win_frac(f_cursor) -> float:
    """Empirical win share over the full train universe (all resolved bets)."""
    global _archive_win_frac, _archive_win_frac_loaded_at
    now = time.time()
    if (
        _archive_win_frac is not None
        and now - _archive_win_frac_loaded_at < ARCHIVE_WIN_FRAC_REFRESH_SECONDS
    ):
        return _archive_win_frac

    sports, factors = universe_sql_params()
    f_cursor.execute(
        f"""
        SELECT
            SUM(CASE WHEN h.is_win = 1 THEN 1 ELSE 0 END)::float
                / NULLIF(COUNT(*), 0) AS frac
        FROM finished_bets h
        JOIN finished_events f ON h.event_id = f.event_id
        WHERE h.is_win IS NOT NULL
        {universe_sql("f", "h")}
        """,
        (sports, factors),
    )
    row = f_cursor.fetchone()
    frac = float(row["frac"]) if row and row.get("frac") is not None else 0.5
    lo = TRAIN_CLASS_QUOTA_MIN_FRAC
    frac = max(lo, min(1.0 - lo, frac))
    _archive_win_frac = frac
    _archive_win_frac_loaded_at = now
    return frac


def _fetch_val_class_rows(
    f_cursor,
    val_event_ids: set,
    is_win: int,
    n: int,
    seen: set,
) -> list[dict[str, Any]]:
    """Up to `n` val-pool rows of one class; deterministic oldest-first order."""
    if n <= 0:
        return []
    sports, factors = universe_sql_params()
    f_cursor.execute(
        f"""
        {_TRAIN_ROW_SELECT}
        AND h.event_id = ANY(%s)
        AND h.is_win = %s
        {universe_sql("f", "h")}
        ORDER BY h.finished_at ASC
        LIMIT %s
        """,
        (list(val_event_ids), is_win, sports, factors, max(n * 2, n + 32)),
    )
    out: list[dict[str, Any]] = []
    for s in _rows_to_train_samples(f_cursor.fetchall()):
        key = s["_key"]
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= n:
            break
    return out


def _fetch_val_batch_raw(f_cursor, val_event_ids: set | None) -> list[dict[str, Any]]:
    if not val_event_ids:
        return []
    win_frac = _get_archive_win_frac(f_cursor)
    n_win = int(round(VAL_BATCH_LIMIT * win_frac))
    n_loss = VAL_BATCH_LIMIT - n_win
    seen: set = set()
    wins = _fetch_val_class_rows(f_cursor, val_event_ids, 1, n_win, seen)
    losses = _fetch_val_class_rows(f_cursor, val_event_ids, 0, n_loss, seen)
    samples = wins + losses
    random.shuffle(samples)
    return samples


_pinned_val_samples: list[dict[str, Any]] | None = None
_pinned_val_loaded_at = 0.0
_pinned_val_event_ids: set | None = None


def _load_val_pin_from_disk() -> list[dict[str, Any]] | None:
    if not os.path.exists(VAL_PIN_PATH):
        return None
    try:
        with open(VAL_PIN_PATH, "r", encoding="utf-8") as f:
            blob = json.load(f)
        if isinstance(blob, dict) and isinstance(blob.get("samples"), list):
            return blob["samples"]
    except Exception as e:
        logger.error(f"Error loading val pin: {e}")
    return None


def _persist_val_pin(samples: list[dict[str, Any]], val_event_ids: set | None) -> None:
    global _pinned_val_samples, _pinned_val_loaded_at, _pinned_val_event_ids
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)
        payload = {
            "pinned_at": time.time(),
            "event_ids": list(val_event_ids or []),
            "samples": samples,
        }
        tmp = VAL_PIN_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, default=str)
        os.replace(tmp, VAL_PIN_PATH)
        _pinned_val_samples = samples
        _pinned_val_loaded_at = time.time()
        _pinned_val_event_ids = set(val_event_ids or [])
        _invalidate_archive_coverage()
    except Exception as e:
        logger.error(f"Error persisting val pin: {e}")


def _val_pin_age_seconds() -> float | None:
    if _pinned_val_loaded_at and _pinned_val_loaded_at > 0:
        return max(0.0, time.time() - _pinned_val_loaded_at)
    return None


def _persist_training_health_snapshot(health: dict[str, Any]) -> None:
    """Write last health verdict so admin can read it while the AI worker is busy."""
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)
        payload = {
            **health,
            "snapshot_at": now_moscow().strftime("%Y-%m-%d %H:%M:%S"),
            "stale": False,
        }
        tmp = TRAINING_HEALTH_SNAPSHOT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, default=str)
        os.replace(tmp, TRAINING_HEALTH_SNAPSHOT_PATH)
    except Exception as e:
        logger.error(f"Error persisting training health snapshot: {e}")


def _load_training_health_snapshot() -> dict[str, Any] | None:
    try:
        with open(TRAINING_HEALTH_SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            blob = json.load(f)
        if isinstance(blob, dict) and blob.get("status") is not None:
            blob = dict(blob)
            blob["stale"] = True
            return blob
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.error(f"Error loading training health snapshot: {e}")
    return None


def _fetch_val_batch(f_cursor, val_event_ids: set | None) -> list[dict[str, Any]]:
    """Pinned held-out slice — same rows and deterministic cutoffs between passes."""
    global _pinned_val_samples, _pinned_val_loaded_at, _pinned_val_event_ids
    now = time.time()
    ids = val_event_ids or set()
    if (
        _pinned_val_samples
        and now - _pinned_val_loaded_at < VAL_PIN_REFRESH_SECONDS
    ):
        age_h = (now - _pinned_val_loaded_at) / 3600.0
        add_ai_log(
            "TRAINING",
            f"Val pin hit (memory) — {len(_pinned_val_samples)} samples / "
            f"{len(_pinned_val_event_ids or ())} events, age {age_h:.1f}h "
            f"(refresh every {VAL_PIN_REFRESH_SECONDS / 3600.0:.0f}h).",
        )
        return list(_pinned_val_samples)
    disk = _load_val_pin_from_disk()
    if disk:
        try:
            with open(VAL_PIN_PATH, "r", encoding="utf-8") as f:
                meta = json.load(f)
            disk_ids = set(meta.get("event_ids") or [])
            pinned_at = float(meta.get("pinned_at") or 0)
            if now - pinned_at < VAL_PIN_REFRESH_SECONDS:
                _pinned_val_samples = disk
                _pinned_val_event_ids = disk_ids
                _pinned_val_loaded_at = pinned_at
                age_h = (now - pinned_at) / 3600.0
                add_ai_log(
                    "TRAINING",
                    f"Val pin hit (disk) — {len(disk)} samples / {len(disk_ids)} events, "
                    f"age {age_h:.1f}h (refresh every {VAL_PIN_REFRESH_SECONDS / 3600.0:.0f}h).",
                )
                return list(disk)
        except Exception:
            pass
    samples = _fetch_val_batch_raw(f_cursor, val_event_ids)
    _persist_val_pin(samples, val_event_ids)
    add_ai_log(
        "TRAINING",
        f"Val pin refreshed — {len(samples)} samples / {len(ids)} events "
        f"(hold for {VAL_PIN_REFRESH_SECONDS / 3600.0:.0f}h). "
        f"Fixed-val tuner ROI on this slice is not walk-forward OOS.",
    )
    return samples


def _persist_last_tune(tune_metrics: dict[str, Any]) -> None:
    """Snapshot for agent_review: fixed-val tuner ROI vs walk-forward."""
    try:
        dt = tune_metrics.get("decision_threshold") or {}
        bw = tune_metrics.get("blend_weight") or {}
        payload = {
            "generated_at": now_moscow().strftime("%Y-%m-%d %H:%M:%S"),
            "samples": tune_metrics.get("samples"),
            "val_pin_age_s": _val_pin_age_seconds(),
            "val_event_count": len(_pinned_val_event_ids or ()),
            "val_roi_pct": dt.get("val_roi_pct"),
            "val_bets": dt.get("val_bets"),
            "decision_threshold": dt.get("new") if dt.get("new") is not None else dt.get("old"),
            "val_brier": bw.get("val_brier"),
            "val_brier_base": tune_metrics.get("val_brier_base"),
            "blend_market_frozen": bool(tune_metrics.get("blend_market_frozen")),
        }
        os.makedirs(MODEL_DIR, exist_ok=True)
        tmp = LAST_TUNE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, LAST_TUNE_PATH)
    except Exception as e:
        logger.error(f"Error persisting last_tune.json: {e}")


def get_last_tune() -> dict[str, Any] | None:
    if not os.path.exists(LAST_TUNE_PATH):
        return None
    try:
        with open(LAST_TUNE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.error(f"Error reading last_tune.json: {e}")
        return None


def _parse_lgb_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    try:
        text = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _lgb_should_refit(f_cursor) -> tuple[bool, str]:
    """Skip a full 40k refit unless the archive moved or the booster is stale."""
    if not ensemble_engine.lgb_trained:
        return True, "no booster on disk"
    accepted = _parse_lgb_ts(ensemble_engine.lgb_last_accepted_at)
    now = datetime.now(timezone.utc)
    age_h = ((now - accepted.astimezone(timezone.utc)).total_seconds() / 3600.0) if accepted else 999.0
    last_ts = ensemble_engine.lgb_newest_finished_at
    new_count = 0
    if last_ts:
        sports, factors = universe_sql_params()
        f_cursor.execute(
            f"""
            SELECT COUNT(*) AS c
            FROM finished_bets h
            JOIN finished_events f ON h.event_id = f.event_id
            WHERE h.is_win IS NOT NULL
              AND h.finished_at > %s
              {universe_sql("f", "h")}
            """,
            [last_ts, sports, factors],
        )
        row = f_cursor.fetchone()
        new_count = int((row["c"] if row else 0) or 0)
    if new_count >= LGB_MIN_FRESH_SAMPLES:
        return True, f"{new_count} new resolved since last accept (need {LGB_MIN_FRESH_SAMPLES})"
    if age_h >= LGB_MAX_AGE_HOURS:
        return True, f"booster age {age_h:.1f}h ≥ {LGB_MAX_AGE_HOURS}h"
    return False, f"{new_count} new resolved, booster age {age_h:.1f}h"


def _refresh_team_form_cache(f_cursor) -> None:
    """Rolling player form — refreshed with LightGBM refit, not every scrape."""
    try:
        sports, factors = universe_sql_params()
        f_cursor.execute(
            f"""
            SELECT h.is_win, h.factor_id, h.finished_at, f.sport_path, f.team_1, f.team_2
            FROM finished_bets h
            JOIN finished_events f ON h.event_id = f.event_id
            WHERE h.is_win IS NOT NULL {universe_sql("f", "h")}
            ORDER BY h.finished_at DESC
            LIMIT 120000
            """,
            (sports, factors),
        )
        cache = build_team_form_index(list(f_cursor.fetchall()))
        set_team_form_cache(cache)
        tmp = TEAM_FORM_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({f"{a}|{b}|{c}": v for (a, b, c), v in cache.items()}, f)
        os.replace(tmp, TEAM_FORM_PATH)
    except Exception as e:
        logger.error(f"Error refreshing team form cache: {e}")
        if os.path.exists(TEAM_FORM_PATH):
            try:
                with open(TEAM_FORM_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                cache = {}
                for k, v in raw.items():
                    parts = k.split("|", 2)
                    if len(parts) == 3:
                        cache[(int(parts[0]), parts[1], int(parts[2]))] = float(v)
                set_team_form_cache(cache)
            except Exception:
                pass


def _serialize_team_stats_cache(cache: dict) -> dict:
    """JSON-safe dump of team/h2h bucket keys."""
    team_out = {}
    for (ti, sport), obs in (cache.get("team") or {}).items():
        team_out[f"{ti}|{sport}"] = [list(o) for o in obs]
    h2h_out = {}
    for (a, b, sport), obs in (cache.get("h2h") or {}).items():
        h2h_out[f"{a}|{b}|{sport}"] = [list(o) for o in obs]
    return {"team": team_out, "h2h": h2h_out, "window": int(cache.get("window") or 40)}


def _deserialize_team_stats_cache(raw: dict) -> dict:
    team = {}
    for k, v in (raw.get("team") or {}).items():
        parts = str(k).split("|", 1)
        if len(parts) != 2:
            continue
        team[(int(parts[0]), parts[1])] = [tuple(o) for o in v]
    h2h = {}
    for k, v in (raw.get("h2h") or {}).items():
        parts = str(k).split("|", 2)
        if len(parts) != 3:
            continue
        h2h[(int(parts[0]), int(parts[1]), parts[2])] = [tuple(o) for o in v]
    return {"team": team, "h2h": h2h, "window": int(raw.get("window") or 40)}


def _refresh_team_stats_cache(f_cursor) -> None:
    """Rolling team/player match stats KB — LGB refit + inference start."""
    try:
        sports, _factors = universe_sql_params()
        f_cursor.execute(
            """
            SELECT event_id, team_1, team_2, sport_path, score_1, score_2,
                   period_scores_json, finished_at
            FROM finished_events
            WHERE LOWER(TRIM(SPLIT_PART(sport_path, '/', 1))) = ANY(%s)
            ORDER BY finished_at DESC
            LIMIT 80000
            """,
            (sports,),
        )
        cache = build_team_stats_index(list(f_cursor.fetchall()))
        set_team_stats_cache(cache)
        tmp = TEAM_STATS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_serialize_team_stats_cache(cache), f)
        os.replace(tmp, TEAM_STATS_PATH)
    except Exception as e:
        logger.error(f"Error refreshing team stats cache: {e}")
        if os.path.exists(TEAM_STATS_PATH):
            try:
                with open(TEAM_STATS_PATH, "r", encoding="utf-8") as f:
                    set_team_stats_cache(_deserialize_team_stats_cache(json.load(f)))
            except Exception:
                pass


def _ensure_team_stats_cache(f_cursor=None) -> None:
    """Warm KB for live inference: prefer disk snapshot, else DB rebuild."""
    if os.path.exists(TEAM_STATS_PATH):
        try:
            with open(TEAM_STATS_PATH, "r", encoding="utf-8") as f:
                set_team_stats_cache(_deserialize_team_stats_cache(json.load(f)))
                return
        except Exception:
            pass
    if f_cursor is not None:
        _refresh_team_stats_cache(f_cursor)


def _mark_trained(f_cursor, keys: list[tuple]):
    for eid, fid, param, prefix in keys:
        f_cursor.execute(
            """
            UPDATE finished_bets SET trained_count = trained_count + 1
            WHERE event_id = %s AND factor_id = %s AND COALESCE(parameter,'') = COALESCE(%s,'')
              AND COALESCE(market_prefix,'') = COALESCE(%s,'')
        """,
            (eid, fid, param, prefix),
        )


# Settlement of live_bets now happens in backend (database.settle_live_bets), fired the
# moment an event is archived there — see backend/database.py:save_parsed_events. This
# service no longer holds an open connection to autobet_finished.db for the "live"
# account at all; it only proposes candidates and reads its balance back over HTTP
# (see bankroll.fetch_live_balance / bankroll.submit_live_bet_candidates).


def _refresh_market_support() -> dict[tuple, int]:
    """
    Returns {(top_level_sport, factor_id, label): resolved_count} over the whole
    archive, cached for MARKET_SUPPORT_REFRESH_SECONDS. Same (sport, factor_id, label)
    grouping the "Статистика" page's bet-type breakdown uses (label already bakes the
    parameter in — see backend get_bet_type_stats), so "does this market have enough
    history to trust" lines up with the table a human would check. On query failure the
    stale cache (or empty dict = fail-open) is returned rather than raising.
    """
    global _market_support, _market_support_loaded_at
    now = time.time()
    if (
        _market_support
        and now - _market_support_loaded_at < MARKET_SUPPORT_REFRESH_SECONDS
    ):
        return _market_support
    f_conn = None
    try:
        f_conn = _track_conn(get_finished_connection())
        f_cursor = f_conn.cursor()
        f_cursor.execute("""
            SELECT TRIM(SPLIT_PART(f.sport_path, '/', 1)) AS sport, h.factor_id, h.label, COUNT(*) AS c
              FROM finished_bets h
              JOIN finished_events f ON h.event_id = f.event_id
             WHERE h.is_win IS NOT NULL
             GROUP BY 1, 2, 3
        """)
        _market_support = {
            (r["sport"] or "", r["factor_id"], r["label"] or ""): r["c"]
            for r in f_cursor.fetchall()
        }
        _market_support_loaded_at = now
        _untrack_conn(f_conn)
        release_connection(f_conn)
        f_conn = None
    except Exception as e:
        logger.error(f"Error refreshing market support counts: {e}")
    finally:
        if f_conn is not None:
            try:
                _untrack_conn(f_conn)
            except Exception:
                pass
            try:
                release_connection(f_conn)
            except Exception:
                pass
    return _market_support


def _place_live_bets(candidates: list[dict[str, Any]]):
    """
    Opens new live_bets from this cycle's predicted-win outcomes (the model's own
    decision-head verdict — see decision_logit in OddsTrajectoryGRU; outcomes it expects
    to lose never make it into `candidates` at all, see the caller), sized by the same
    fractional-Kelly allocate() the training loss uses — capped-Kelly fraction per
    candidate (bankroll.MAX_POSITION_FRACTION ceiling), scaled by the network's own
    stake head, at most bankroll.MAX_POSITIONS positions and at least
    bankroll.MIN_STAKE_FRACTION of the *available* (unlocked) live balance per position.
    If the live account can't cover even one minimum stake, no bets are placed this
    cycle.
    """
    if not candidates:
        return {"placed": 0, "reason": "no_predicted_win_candidates", "candidates": 0}

    available = bankroll.fetch_live_balance()
    if available <= 0:
        return {
            "placed": 0,
            "reason": "insufficient_available_balance",
            "candidates": len(candidates),
        }

    # Cap the candidate pool before allocating — allocate() would gladly Kelly-size all
    # of them, but only the strongest few are worth considering.
    LIVE_CANDIDATE_POOL = 20
    candidates = sorted(candidates, key=lambda c: c["expected_roi"], reverse=True)[
        :LIVE_CANDIDATE_POOL
    ]
    # win_probs uses the calibrated probability (same number the "Нейроставки" UI shows
    # and the one actually stored/acted on — see calibrate_probability), not the raw
    # ensemble score, so Kelly sizing is staking against the same edge estimate as
    # everything else that reasons about this bet.
    win_probs = torch.tensor(
        [c["win_probability"] / 100.0 for c in candidates], dtype=torch.float32
    )
    coeffs_t = torch.tensor([c["coeff"] for c in candidates], dtype=torch.float32)
    stake_logits = torch.tensor(
        [c["stake_logit"] for c in candidates], dtype=torch.float32
    )
    with torch.no_grad():
        fractions = bankroll.allocate(win_probs, coeffs_t, stake_logits)

    # Sizing (which candidates, how much of the bank) stays a model decision made here.
    # Execution (does this market still exist, does it actually get written, at what
    # exact stake given the balance backend sees right now) belongs to backend — see
    # database.place_live_bet_candidates, which re-validates freshness before writing.
    to_submit = [
        {
            "event_id": c["event_id"],
            "factor_id": c["factor_id"],
            "market_prefix": c["market_prefix"],
            "parameter": c["parameter"],
            "label": c.get("label", ""),
            "match_name": c.get("match_name", ""),
            "coefficient": c["coeff"],
            "stake_fraction": frac,
            "win_probability": c["win_probability"],
        }
        for c, frac in zip(candidates, fractions.tolist())
        if frac >= bankroll.MIN_STAKE_FRACTION
    ]

    if not to_submit:
        # allocate() found candidates but every fraction fell below the 10%-of-bank
        # floor (bankroll.MIN_STAKE_FRACTION) — usually because the stake head hasn't
        # learned to concentrate exposure on a favorite yet, so softmax spreads it too
        # thin across the candidate pool. Surface the actual numbers so this is
        # diagnosable from the admin log instead of silently doing nothing.
        best_frac = max(fractions.tolist()) if len(fractions) else 0.0
        return {
            "placed": 0,
            "reason": "all_fractions_below_min_stake",
            "candidates": len(candidates),
            "best_fraction_pct": round(best_frac * 100.0, 2),
        }

    result = bankroll.submit_live_bet_candidates(to_submit)
    placed_bets = result.get("placed", []) or []
    return {
        "placed": len(placed_bets),
        "placed_bets": placed_bets,
        "candidates": len(candidates),
        "skipped": result.get("skipped", []),
    }


def run_neuralbet_inference_and_training(
    scrape_timestamp: str | None = None,
) -> dict[str, Any]:
    global _cycle_count
    if cycle_aborted():
        add_ai_log("SYSTEM", "AI cycle skipped — model reset in progress.")
        return {
            "status": "aborted",
            "predictions_count": 0,
            "finished_samples_trained": 0,
        }
    with _engine_lock:
        if cycle_aborted():
            add_ai_log("SYSTEM", "AI cycle skipped — model reset in progress.")
            return {
                "status": "aborted",
                "predictions_count": 0,
                "finished_samples_trained": 0,
            }
        try:
            return _run_neuralbet_inference_and_training_locked(scrape_timestamp)
        except Exception as e:
            if cycle_aborted() or _is_query_canceled(e):
                add_ai_log(
                    "SYSTEM",
                    "AI cycle aborted so the neural network can reset.",
                    level="WARNING",
                )
                return {
                    "status": "aborted",
                    "predictions_count": 0,
                    "finished_samples_trained": 0,
                }
            raise
        finally:
            _release_tracked_conns()


def _run_live_inference_and_bets(scrape_timestamp: str | None) -> list[dict[str, Any]]:
    """Live odds -> calibrated predictions -> Kelly. Skipped during cold-start."""
    # Refresh team-stats KB before live views (same finished_events path as LGB).
    try:
        stats_conn = _track_conn(get_finished_connection())
        try:
            _refresh_team_stats_cache(stats_conn.cursor())
        finally:
            _untrack_conn(stats_conn)
            release_connection(stats_conn)
    except Exception as e:
        logger.error(f"Error refreshing team stats cache at inference start: {e}")
        _ensure_team_stats_cache()

    conn = _track_conn(get_connection())
    cursor = conn.cursor()

    # l.updated_at = e.last_updated_at restricts this to markets that were actually
    # present in the event's most recent scrape snapshot. Without it, a market the
    # bookmaker has since pulled (e.g. a period-scoped market once that period ends)
    # keeps its last-known coefficient frozen in latest_odds forever — the event stays
    # is_live=1 (other markets on it keep refreshing), so this query would otherwise
    # keep feeding a no-longer-offered market to the model as if it were live.
    # e.last_updated_at pinned to scrape_timestamp (the exact cycle backend just
    # committed and told us about — see routes.py/backend main.py's trigger_ai_pipeline)
    # additionally catches an event that's vanished from Fonbet's feed *entirely* (grace
    # period hasn't finalized it yet): both timestamps freeze together in that case, so
    # comparing them only to each other never notices. Falls back to MAX(last_updated_at)
    # only if this was invoked without a pinned timestamp (e.g. ai_service restarted and
    # something calls this directly) — that's still "freshest available," just not
    # provably tied to a specific completed scrape.
    if scrape_timestamp:
        target_ts = scrape_timestamp
    else:
        cursor.execute("SELECT MAX(last_updated_at) FROM events")
        target_ts = cursor.fetchone()["max"]
    # Combat sports (единоборства/MMA/UFC/бокс) and compressed sims (NBA 2K /
    # Esportsbattle 4Х5, Setka Cup, "NxM мин") are excluded from betting: the live
    # list either lags (combat) or the match finishes faster than a scrape (2K),
    # so we would settle against a frozen mid-game score. Defense-in-depth in case
    # such an event was already stored before the parser-level skip took effect.
    sports, factors = universe_sql_params()
    cursor.execute(
        f"""
        SELECT
            l.event_id, l.factor_id, l.market_prefix, l.label, l.parameter, l.coefficient,
            COALESCE(l.initial_coefficient, l.coefficient) AS initial_coefficient,
            e.sport_path, e.match_name, e.score_1, e.score_2, e.timer, e.team_1, e.team_2
        FROM latest_odds l
        JOIN events e ON l.event_id = e.event_id
        WHERE e.is_live = 1
          AND l.updated_at = e.last_updated_at
          AND e.last_updated_at = %s
          AND e.sport_path !~* '(единоборства|mma|ufc|бокс)'
          AND e.sport_path !~* '{FAST_FORMAT_SPORT_SQL}'
          {universe_sql("e", "l")}
    """,
        (target_ts, sports, factors),
    )
    live_odds_rows = cursor.fetchall()

    # Pull the whole odds+score history for these events in one query and group it in
    # Python, instead of one query per row. Score is carried alongside the coefficient
    # at every step (score_at_time), not the final match score — the model must be
    # trained and served on the same kind of "as it looked live" trajectory, or the
    # LightGBM/PyTorch accuracy numbers are measuring a leak, not real skill.
    event_ids = list({row["event_id"] for row in live_odds_rows})
    trajectory_map: dict[tuple, list[tuple[float, int, int, float | None, Any]]] = {}
    if event_ids:
        cursor.execute(
            """
            SELECT event_id, factor_id, parameter, market_prefix, coefficient, score_at_time, timestamp, timer_at_time
            FROM odds_history
            WHERE event_id = ANY(%s)
            ORDER BY id ASC
        """,
            (event_ids,),
        )
        for h in cursor.fetchall():
            key = (
                h["event_id"],
                h["factor_id"],
                str(h["parameter"] or ""),
                h["market_prefix"] or "",
            )
            trajectory_map.setdefault(key, []).append(
                (
                    float(h["coefficient"]),
                    parse_score_diff(h["score_at_time"]),
                    parse_score_sum(h["score_at_time"]),
                    parse_ts_epoch(h["timestamp"]),
                    h["timer_at_time"],
                )
            )

    overround_map: dict = {}
    if event_ids:
        cursor.execute(
            """
            SELECT event_id, factor_id, COALESCE(parameter, '') AS parameter,
                   COALESCE(market_prefix, '') AS market_prefix, coefficient
            FROM latest_odds
            WHERE event_id = ANY(%s)
        """,
            (event_ids,),
        )
        overround_map = accumulate_overround(cursor.fetchall())

    batch_items = []
    row_meta = []
    for row in live_odds_rows:
        if not in_train_universe(row["sport_path"], row["factor_id"], row["parameter"]):
            continue
        eid = row["event_id"]
        fid = row["factor_id"]
        prefix = row["market_prefix"] or ""
        param = str(row["parameter"] or "")
        coeff = float(row["coefficient"] or 1.0)
        s1 = int(row["score_1"] or 0)
        s2 = int(row["score_2"] or 0)

        traj = trajectory_map.get((eid, fid, param, prefix)) or [
            (coeff, s1 - s2, s1 + s2, None, row["timer"])
        ]
        ov_key = overround_group_key(fid, param, prefix)
        overround = overround_map.get((eid, ov_key)) if ov_key else None

        view = build_model_input(
            live_sample(
                coeffs=[t[0] for t in traj],
                score_diffs=[t[1] for t in traj],
                score_sums=[t[2] for t in traj],
                timestamps=[t[3] for t in traj],
                timer_raws=[t[4] for t in traj],
                factor_id=fid,
                parameter=param,
                market_prefix=prefix,
                sport_path=row["sport_path"] or "",
                team_1=row["team_1"] or "",
                team_2=row["team_2"] or "",
                overround=overround,
                event_id=eid,
            ),
            mode="serve",
        )
        if view is None:
            continue
        view["current_coeff"] = coeff
        view["coefficient"] = coeff
        batch_items.append(view)
        row_meta.append(
            {
                "event_id": eid,
                "factor_id": fid,
                "market_prefix": prefix,
                "parameter": param,
                "coeff": coeff,
                "label": row["label"] or "",
                "match_name": row["match_name"] or "",
                "sport": (row["sport_path"] or "").split("/")[0].strip() or None,
                "team_1": row["team_1"] or "",
                "team_2": row["team_2"] or "",
                "score": f"{s1}:{s2}",
                "score_1": s1,
                "score_2": s2,
                "initial_coeff": float(row.get("initial_coefficient") or coeff),
            }
        )

    predictions = []
    live_candidates = []
    timestamp_str = now_moscow().isoformat()

    batch_results = ensemble_engine.predict_batch(batch_items)

    # Bucket table fetched once per cycle (not per-row) — same Bayesian-shrinkage
    # correction the "Нейроставки" UI used to compute separately in backend, now the
    # single source of truth: whatever gets stored/bet on is this number, not the
    # ensemble's raw one. Buckets are per-sport (see calibration.py) so calibration
    # itself is still one lookup per row.
    buckets = get_calibration_buckets()

    predicted_win_count = 0
    predicted_loss_count = 0
    skipped_low_edge = 0
    skipped_low_support = 0
    skipped_coeff = 0
    skipped_sport = 0
    skipped_market = 0
    skipped_will_win = 0
    market_support = _refresh_market_support()

    # Calibrate first, then sibling coherence (soft sum-to-1 + 2-way EV veto),
    # then EV/predicted_win and live gates — same helper as backtest (parity).
    coherence_rows: list[dict[str, Any]] = []
    for meta, (
        win_prob,
        error_rate,
        lgb_score,
        torch_score,
        decision_prob,
        stake_logit,
        exposure_logit,
    ) in zip(row_meta, batch_results):
        coeff = meta["coeff"]
        calibrated_prob = calibrate_probability(
            win_prob, buckets, sport=meta["sport"], coeff=coeff
        )
        coherence_rows.append(
            {
                **meta,
                "calibrated_p": calibrated_prob / 100.0,
                "coeff": coeff,
                "_win_prob": win_prob,
                "_lgb_score": lgb_score,
                "_torch_score": torch_score,
                "_decision_prob": decision_prob,
                "_stake_logit": stake_logit,
            }
        )

    apply_sibling_coherence(coherence_rows, min_edge_pct=MIN_BET_EDGE_PCT)

    for row in coherence_rows:
        eid = row["event_id"]
        fid = row["factor_id"]
        prefix = row["market_prefix"]
        param = row["parameter"]
        coeff = row["coeff"]
        calibrated_prob = float(row["calibrated_p"]) * 100.0
        expected_roi = float(row.get("expected_roi") or 0.0)
        predicted_win = int(row.get("predicted_win") or 0)
        decision_prob = float(row["_decision_prob"])
        stake_logit = row["_stake_logit"]
        lgb_score = float(row["_lgb_score"])
        torch_score = float(row["_torch_score"])

        if predicted_win:
            predicted_win_count += 1
        else:
            predicted_loss_count += 1

        predictions.append(
            {
                "event_id": eid,
                "factor_id": fid,
                "market_prefix": prefix,
                "parameter": param,
                "win_probability": round(calibrated_prob, 1),
                "error_rate": round(100.0 - calibrated_prob, 1),
                "expected_roi": round(expected_roi, 1),
                "lightgbm_score": round(lgb_score, 3),
                "pytorch_score": round(torch_score, 3),
                "predicted_win": predicted_win,
                "decision_confidence": round(decision_prob, 3),
            }
        )

        will_win = outcome_will_win(
            predicted_win,
            coeff,
            factor_id=fid,
            score_1=row.get("score_1"),
            score_2=row.get("score_2"),
            win_probability=calibrated_prob,
        )
        if will_win != 1:
            if predicted_win:
                skipped_will_win += 1
            continue
        support_count = None
        if market_support:
            support_count = market_support.get(
                (row["sport"] or "", fid, row["label"] or ""), 0
            )
        reason = live_gate_skip_reason(
            coeff,
            expected_roi,
            support_count,
            row.get("sport"),
            factor_id=fid,
            win_probability=calibrated_prob,
            will_win=will_win,
        )
        if reason == "will_win":
            skipped_will_win += 1
        elif reason == "coeff":
            skipped_coeff += 1
        elif reason == "edge":
            skipped_low_edge += 1
        elif reason == "support":
            skipped_low_support += 1
        elif reason == "sport":
            skipped_sport += 1
        elif reason == "market":
            skipped_market += 1
        elif reason is None:
            live_candidates.append(
                {
                        "event_id": eid,
                        "factor_id": fid,
                        "market_prefix": prefix,
                        "parameter": param,
                        "coeff": coeff,
                        "label": row["label"] or "",
                        "match_name": row["match_name"] or "",
                        "sport": row.get("sport"),
                        "team_1": row["team_1"] or "",
                        "team_2": row["team_2"] or "",
                        "score": row["score"],
                        "win_probability": round(calibrated_prob, 1),
                        "expected_roi": expected_roi,
                        "stake_logit": stake_logit,
                        "decision_confidence": round(decision_prob, 3),
                    }
                )

    if (
        skipped_low_edge
        or skipped_low_support
        or skipped_coeff
        or skipped_sport
        or skipped_market
        or skipped_will_win
    ):
        sport_part = (
            f", {skipped_sport} — sport outside live list "
            f"(NEURALBET_LIVE_STAKE_SPORTS ∩ Brier-vs-market gate)"
            if skipped_sport
            else ""
        )
        market_part = (
            f", {skipped_market} — market outside live list (NEURALBET_LIVE_STAKE_MARKETS)"
            if skipped_market
            else ""
        )
        add_ai_log(
            "BANKROLL",
            f"Bet candidates filtered: {skipped_will_win} — not will_win, "
            f"{skipped_coeff} — coeff outside "
            f"{MIN_BET_COEFF:.1f}–{MAX_BET_COEFF:.1f} "
            f"(or {MAX_BET_COEFF:.1f}–{MAX_BET_COEFF_HIGH_P:.1f} with p<{HIGH_P_STAKE:.0%}), "
            f"{skipped_low_edge} — EV below {MIN_BET_EDGE_PCT:.0f}%, "
            f"{skipped_low_support} — market has fewer than {MIN_MARKET_SUPPORT} resolved archive outcomes"
            f"{sport_part}{market_part}. "
            f"Remaining {len(live_candidates)}.",
        )

    if predictions:
        save_ai_predictions(predictions, timestamp_str)
        add_ai_log(
            "INFERENCE",
            f"Evaluated predictions for {len(predictions)} active live outcomes — "
            f"{predicted_win_count} verdict 'bet' / {predicted_loss_count} verdict 'skip'. "
            "(PyTorch & LightGBM scores saved)",
        )
    _untrack_conn(conn)
    release_connection(conn)

    # Live bankroll pipeline: EV + live gates → quality gate (or bypass) → Kelly.
    if _load_cold_start().get("active"):
        add_ai_log(
            "BANKROLL",
            "Cold-start in progress — live bets skipped (weights are random "
            "until the archive walk finishes).",
        )
        place_result = {}
    else:
        quality_fail = _live_quality_skip_reason()
        if quality_fail:
            add_ai_log(
                "BANKROLL",
                "Live bets skipped — quality gate: " + quality_fail + ".",
                level="WARNING",
            )
            place_result = {"placed": 0, "reason": "quality_gate"}
        else:
            bypass_on = bool(AI_SETTINGS.get("quality_gate_bypass"))
            if bypass_on:
                gate_now = get_live_quality_gate()
                if not gate_now.get("pass"):
                    add_ai_log(
                        "BANKROLL",
                        "Quality gate bypass ON — gate skipped ("
                        + "; ".join(gate_now.get("reasons") or ["unknown"])
                        + ").",
                        level="WARNING",
                    )
                else:
                    add_ai_log(
                        "BANKROLL",
                        "Quality gate bypass ON (gate already pass).",
                    )
            place_result = _place_live_bets(live_candidates)

    if place_result.get("placed"):
        skip_reasons = [s.get("reason") for s in place_result.get("skipped", [])]
        skipped_stale = skip_reasons.count("stale_market")
        skipped_conflict = skip_reasons.count("event_already_has_open_bet")
        extra_bits = []
        if skipped_stale:
            extra_bits.append(f"{skipped_stale} stale")
        if skipped_conflict:
            # backend refused to open a second position on a match that already has an
            # open bet (see place_live_bet_candidates' occupied_events) — worth calling
            # out specifically since it means the model proposed betting on more than
            # one market of the same live event this cycle.
            extra_bits.append(f"{skipped_conflict} event already has open bet")
        extra = f" ({', '.join(extra_bits)} skipped)" if extra_bits else ""
        add_ai_log(
            "BANKROLL",
            f"Live bankroll: opened {place_result['placed']} new bet(s) this cycle.{extra}",
        )
    else:
        reason = place_result.get("reason")
        if reason == "no_predicted_win_candidates":
            add_ai_log(
                "BANKROLL",
                "Live bankroll: 0 bets — model has no predicted-win outcomes this cycle.",
            )
        elif reason == "insufficient_available_balance":
            add_ai_log(
                "BANKROLL",
                "Live bankroll: 0 bets — available balance is 0 (all locked or ruined).",
                level="WARNING",
            )
        elif reason == "all_fractions_below_min_stake":
            add_ai_log(
                "BANKROLL",
                f"Live bankroll: 0 bets — {place_result['candidates']} predicted-win candidate(s) found, "
                f"but the allocator's best fraction was only {place_result['best_fraction_pct']}% "
                f"of bank (needs >= {bankroll.MIN_STAKE_FRACTION * 100:.0f}%). "
                "Stake head hasn't concentrated exposure on a favorite yet — expected while undertrained.",
                level="WARNING",
            )
        elif reason == "quality_gate":
            pass
    return predictions


def _run_neuralbet_inference_and_training_locked(
    scrape_timestamp: str | None = None,
) -> dict[str, Any]:
    global _cycle_count, _low_epoch_streak, _checkpoint_reject_streak
    _cycle_count += 1
    logger.info(
        f"AI cycle {_cycle_count} start "
        f"(inference={'on' if AI_SETTINGS['ai_enabled'] else 'off'}, "
        f"training={'on' if AI_SETTINGS['training_enabled'] else 'off'}, "
        f"scrape_ts={scrape_timestamp})"
    )
    add_ai_log(
        "INFERENCE",
        f"Cycle {_cycle_count} started "
        f"(inference={'on' if AI_SETTINGS['ai_enabled'] else 'off'}, "
        f"training={'on' if AI_SETTINGS['training_enabled'] else 'off'}, "
        f"scrape_ts={scrape_timestamp or 'latest'}).",
    )

    if not AI_SETTINGS["ai_enabled"]:
        add_ai_log(
            "INFERENCE", "AI Inference skipped (Disabled by Admin).", level="WARNING"
        )
        return {
            "status": "disabled",
            "predictions_count": 0,
            "finished_samples_trained": 0,
        }

    if cycle_aborted():
        add_ai_log("SYSTEM", "AI cycle aborted before inference.")
        return {
            "status": "aborted",
            "predictions_count": 0,
            "finished_samples_trained": 0,
        }

    predictions: list[dict[str, Any]] = []
    if _load_cold_start().get("active"):
        add_ai_log(
            "INFERENCE",
            "Live inference skipped — cold-start archive walk in progress "
            "(weights are random until the walk finishes).",
        )
        add_ai_log(
            "BANKROLL",
            "Cold-start in progress — live bets skipped (weights are random "
            "until the archive walk finishes).",
        )
    else:
        predictions = _run_live_inference_and_bets(scrape_timestamp)


    if cycle_aborted():
        add_ai_log("SYSTEM", "AI cycle aborted after inference — skipping training.")
        return {
            "status": "aborted",
            "predictions_count": len(predictions),
            "finished_samples_trained": 0,
        }

    if not AI_SETTINGS["training_enabled"]:
        add_ai_log(
            "TRAINING",
            "Online Retraining skipped (Disabled by Admin).",
            level="WARNING",
        )
        return {"predictions_count": len(predictions), "finished_samples_trained": 0}

    # Force cycle 1 too (cold start — otherwise every one of these stays at its
    # untrained/untuned default for the first TRAIN_EVERY_CYCLES/TUNE_EVERY_CYCLES/
    # LGB_REFIT_EVERY_CYCLES cycles after every restart). Catch-up shortens only the
    # GRU pass (see TRAIN_CATCHUP_EVERY_CYCLES); tuner/LightGBM stay on their cadence.
    coverage = get_archive_training_coverage()
    cold_start = _load_cold_start()
    cold_start_active = bool(cold_start.get("active"))
    if (
        cold_start_active
        and int(cold_start.get("samples_this_epoch") or 0) > 0
        and not ensemble_engine.checkpoint_window_active
    ):
        # The process restarted mid-epoch. Interim chunk weights intentionally are
        # not an accepted checkpoint, so replay this epoch from offset zero rather
        # than skipping rows whose in-memory updates were lost.
        cold_start["samples_this_epoch"] = 0
        _save_cold_start(cold_start)
        add_ai_log(
            "TRAINING",
            "Cold-start worker restarted mid-epoch — replaying the current "
            "streaming epoch from offset 0.",
            level="WARNING",
        )
    train_every = int(coverage["train_every_cycles"])
    is_train_cycle = (
        True
        if cold_start_active
        else (_cycle_count == 1 or _cycle_count % train_every == 0)
    )
    # LightGBM/tuner want their own extra 40k fetch — on top of a cold-start chunk
    # that already saturates RAM and the connection pool. Refit after the walk.
    is_tune_cycle = (
        False
        if cold_start_active
        else (_cycle_count == 1 or _cycle_count % TUNE_EVERY_CYCLES == 0)
    )
    is_lgb_cycle = (
        False
        if cold_start_active
        else (
            ((not ensemble_engine.lgb_trained) and _cycle_count == 1)
            or _cycle_count % LGB_REFIT_EVERY_CYCLES == 0
        )
    )

    gru_at_reject_limit = (
        not cold_start_active
        and _checkpoint_reject_streak >= CHECKPOINT_REJECT_STREAK_ALERT
    )
    # Hard freeze would deadlock catch-up: streak only refreshes after a GRU pass.
    # Probe periodically so an accept can clear the streak; a reject re-enters cooldown.
    gru_probe_due = (
        gru_at_reject_limit
        and CHECKPOINT_REJECT_PROBE_EVERY_CYCLES > 0
        and _cycle_count % CHECKPOINT_REJECT_PROBE_EVERY_CYCLES == 0
    )
    gru_frozen = gru_at_reject_limit and not gru_probe_due
    run_gru_training = (is_train_cycle or gru_probe_due) and not gru_frozen
    if (
        run_gru_training
        and not cold_start_active
        and int(coverage.get("untrained") or 0) < MIN_FRESH_SAMPLES
    ):
        add_ai_log(
            "TRAINING",
            f"Online GRU skipped — only {coverage.get('untrained', 0)} unseen train-pool "
            f"rows (need {MIN_FRESH_SAMPLES}+ fresh). Replay-only passes are disabled.",
        )
        run_gru_training = False
    gru_pass_rejected = False

    training_samples: list[dict[str, Any]] = []
    val_samples: list[dict[str, Any]] = []
    train_keys: list[tuple] = []
    class_mix: dict[str, int] | None = None
    lgb_rows: list[dict[str, Any]] = []
    lgb_val_rows: list[dict[str, Any]] = []
    try:
        f_conn = _track_conn(get_finished_connection())
        f_cursor = f_conn.cursor()

        candidate_val_event_ids = _get_val_event_ids(f_cursor)
        if run_gru_training or is_tune_cycle or is_lgb_cycle:
            val_samples = _fetch_val_batch(f_cursor, candidate_val_event_ids)
        # Exclude the same pinned events from training for the whole pin lifetime;
        # otherwise a sliding candidate window eventually leaks validation rows into
        # the train pool while metrics still evaluate those pinned rows.
        val_event_ids = (
            set(_pinned_val_event_ids)
            if _pinned_val_event_ids is not None
            else candidate_val_event_ids
        )
        # Skipping the fetch on a non-training cycle isn't just "do less work" — it's
        # what makes the accumulation in TRAIN_EVERY_CYCLES's docstring actually happen:
        # matches that finish in between stay trained_count = 0 (see _mark_trained,
        # only called after a training cycle) until the next training cycle picks them
        # all up together as one larger batch.
        if run_gru_training:
            if cold_start_active:
                cold_start = _ensure_cold_start_pool(
                    f_cursor, cold_start, val_event_ids,
                )
                pool_event_ids = cold_start.get("pool_event_ids")
                if not isinstance(pool_event_ids, list):
                    pool_event_ids = None
                cs_epoch = int(cold_start.get("epoch") or 1)
                add_ai_log(
                    "TRAINING",
                    f"Loading cold-start batch ({_cold_start_chunk_label()}, "
                    f"epoch {cs_epoch}/{cold_start.get('epochs_total', COLD_START_EPOCHS)}, "
                    f"seen {cold_start.get('samples_this_epoch', 0)}/"
                    f"{cold_start.get('train_pool_size', '?')}, "
                    f"pool events {cold_start.get('pool_event_count', '?')}, "
                    f"archive {coverage.get('untrained', '?')} unseen)...",
                )
                training_samples, train_keys = _fetch_cold_start_batch(
                    f_cursor, val_event_ids, cs_epoch,
                    offset=int(cold_start.get("samples_this_epoch") or 0),
                    pool_event_ids=pool_event_ids,
                )
            else:
                add_ai_log(
                    "TRAINING",
                    f"Loading training batch (target {TRAIN_BATCH_TOTAL}, "
                    f"archive {coverage.get('untrained', '?')} unseen)...",
                )
                training_samples, train_keys, class_mix = _fetch_training_batch(
                    f_cursor, val_event_ids
                )
        if is_lgb_cycle:
            should_lgb, lgb_reason = _lgb_should_refit(f_cursor)
            if not should_lgb:
                add_ai_log("TRAINING", f"LightGBM refit skipped — {lgb_reason}.")
            else:
                add_ai_log("TRAINING", f"LightGBM refit starting — {lgb_reason}.")
                _refresh_team_form_cache(f_cursor)
                _refresh_team_stats_cache(f_cursor)
                f_cursor.execute(
                    f"""
                    {_TRAIN_ROW_SELECT}
                      AND (%s::bigint[] IS NULL OR h.event_id != ALL(%s))
                    {universe_sql("f", "h")}
                    ORDER BY h.finished_at DESC
                    LIMIT %s
                """,
                    (
                        list(val_event_ids) if val_event_ids else None,
                        list(val_event_ids) if val_event_ids else [],
                        *universe_sql_params(),
                        LGB_TRAIN_LIMIT,
                    ),
                )
                lgb_rows = [
                    s for s in (_row_to_sample(r) for r in f_cursor.fetchall())
                    if in_train_universe(s["sport_path"], s["factor_id"], s.get("parameter"))
                ]
                lgb_val_rows = val_samples

        _untrack_conn(f_conn)
        release_connection(f_conn)
    except Exception as e:
        logger.error(f"Error querying finished training db: {e}")
        if cycle_aborted() or _is_query_canceled(e):
            add_ai_log("SYSTEM", "Training fetch aborted for model reset.", level="WARNING")
            return {
                "status": "aborted",
                "predictions_count": len(predictions),
                "finished_samples_trained": 0,
            }

    fetched_train_count = len(training_samples)
    min_needed = 2 if cold_start_active else MIN_TRAIN_SAMPLES
    insufficient_for_training = (
        training_samples and fetched_train_count < min_needed
    )
    if gru_probe_due and run_gru_training:
        add_ai_log(
            "TRAINING",
            f"Online GRU probe — checkpoint rejected {_checkpoint_reject_streak} pass(es) "
            f"in a row (threshold {CHECKPOINT_REJECT_STREAK_ALERT}); attempting one pass "
            f"(probe every {CHECKPOINT_REJECT_PROBE_EVERY_CYCLES} cycles). "
            "Accept clears the freeze; reject re-enters cooldown.",
            level="WARNING",
        )
    elif gru_frozen and is_train_cycle:
        add_ai_log(
            "TRAINING",
            f"Online GRU skipped — checkpoint rejected {_checkpoint_reject_streak} pass(es) "
            f"in a row (threshold {CHECKPOINT_REJECT_STREAK_ALERT}); weights frozen, "
            f"probe every {CHECKPOINT_REJECT_PROBE_EVERY_CYCLES} cycles. "
            "LightGBM/tuner still run this cycle.",
            level="WARNING",
        )
    if insufficient_for_training:
        add_ai_log(
            "TRAINING",
            f"Skipping training step — only {fetched_train_count} samples available "
            f"(need {min_needed}+; below that, one epoch is enough to memorize the "
            "whole batch instead of learning anything general). Rows stay unmarked and "
            "will be retried, with whatever's newly finished added in, next training cycle.",
            level="WARNING",
        )
        training_samples = []

    replay_only = (
        not cold_start_active
        and bool(training_samples)
        and class_mix is not None
        and int(class_mix.get("got_fresh") or 0) < MIN_FRESH_SAMPLES
    )
    if replay_only:
        add_ai_log(
            "TRAINING",
            f"Skipping training step — batch is replay-only "
            f"({class_mix.get('got_fresh', 0)} fresh / {class_mix.get('got_replay', 0)} replay, "
            f"need {MIN_FRESH_SAMPLES}+ fresh). Rows stay unmarked.",
            level="WARNING",
        )
        training_samples = []
        train_keys = []

    if (
        cold_start_active
        and is_train_cycle
        and not training_samples
        and not insufficient_for_training
    ):
        pool = int(cold_start.get("train_pool_size") or 0)
        if pool <= 0 or int(cold_start.get("samples_this_epoch") or 0) >= pool:
            cold_start = _advance_cold_start(
                cold_start, max(pool - int(cold_start.get("samples_this_epoch") or 0), 0)
            )
        else:
            add_ai_log(
                "TRAINING",
                "Cold-start fetch returned 0 rows — advancing to the next archive epoch.",
            )
            cold_start["samples_this_epoch"] = pool or int(
                cold_start.get("samples_this_epoch") or 0
            )
            cold_start = _advance_cold_start(cold_start, 0)

    if training_samples:
        cov_str = ""
        if coverage.get("total"):
            cov_str = (
                f" Archive {coverage['trained_ratio']:.0%} trained "
                f"({coverage['trained']}/{coverage['total']}"
                f"{', catch-up' if coverage.get('catch_up') else ''})."
            )
        if cold_start_active:
            cs_epoch = int(cold_start.get("epoch") or 1)
            mix_str = (
                f"natural mix, lr={_cold_start_lr(cs_epoch):.4g}, "
                f"streaming pass (no val/checkpoint until epoch end)"
            )
            start_msg = (
                f"Starting cold-start chunk: {len(training_samples)} samples "
                f"(epoch {cs_epoch}/{cold_start.get('epochs_total', COLD_START_EPOCHS)}, "
                f"{mix_str}) "
            )
        else:
            if class_mix:
                mix_str = (
                    f"target mix {class_mix['target_win']}/{class_mix['target_loss']} "
                    f"got {class_mix['got_win']} win / {class_mix['got_loss']} loss"
                )
            else:
                mix_str = (
                    f"{int(len(training_samples) * TRAIN_FRESH_SHARE)} fresh target / rest replay"
                )
            fresh_n = (class_mix or {}).get("got_fresh")
            replay_n = (class_mix or {}).get("got_replay")
            if fresh_n is not None:
                mix_str += f", {fresh_n} fresh / {replay_n} replay"
            stake_n = (class_mix or {}).get("got_stake_sport")
            if stake_n is not None:
                mix_str += f", {stake_n} live-stake-sport"
            start_msg = (
                f"Starting online training pass: {len(training_samples)} samples "
                f"({mix_str}), {len(val_samples)} held out for validation "
            )
        add_ai_log(
            "TRAINING",
            start_msg
            + "(universe: football/basketball/table tennis/volleyball/tennis × W1/W2 "
            f"+ football draw + match totals; coeffs 1.5–2.0).{cov_str}",
        )

        def _log_epoch(epoch_idx: int, train_loss: float, val_loss: float | None):
            if val_loss is not None:
                add_ai_log(
                    "TRAINING",
                    f"Epoch {epoch_idx} — train_loss: {train_loss:.4f}, val_brier: {val_loss:.4f}",
                )
            else:
                add_ai_log(
                    "TRAINING", f"Epoch {epoch_idx} — train_loss: {train_loss:.4f}"
                )

        cs_epoch = int(cold_start.get("epoch") or 1) if cold_start_active else 1
        pool = int(cold_start.get("train_pool_size") or 0) if cold_start_active else 0
        seen = int(cold_start.get("samples_this_epoch") or 0) if cold_start_active else 0
        epoch_complete = (
            cold_start_active
            and pool > 0
            and seen + len(training_samples) >= pool
        )
        if cold_start_active and seen == 0:
            baseline_loss = ensemble_engine.begin_checkpoint_window(val_samples)
            if baseline_loss is None:
                raise RuntimeError(
                    "Cold-start cannot pin epoch checkpoint baseline without validation data"
                )
            add_ai_log(
                "TRAINING",
                f"Cold-start epoch {cs_epoch} baseline pinned: "
                f"val_brier {baseline_loss:.4f}.",
            )
        global _online_pass_count
        use_bankroll = _online_pass_count >= int(
            os.getenv("NEURALBET_BANKROLL_LOSS_DISABLED_PASSES", "0")
        )
        try:
            if cold_start_active:
                metrics = ensemble_engine.train_cold_start_chunk(
                    training_samples,
                    learning_rate=_cold_start_lr(cs_epoch),
                    rebalance_classes=True,
                    should_abort=cycle_aborted,
                )
                if epoch_complete:
                    epoch_gate = ensemble_engine.finish_checkpoint_window(
                        val_samples,
                        best_epoch=cs_epoch,
                    )
                    metrics.update(epoch_gate)
                    metrics["best_epoch"] = cs_epoch
                else:
                    metrics["checkpoint_accepted"] = True
                    metrics["checkpoint_saved"] = False
                    metrics["checkpoint_deferred"] = True
                    metrics["checkpoint_reject_reason"] = None
                    metrics["val_loss"] = None
                    metrics["val_guess_rate"] = None
            else:
                metrics = ensemble_engine.train_online(
                    training_samples,
                    val_data=val_samples,
                    on_epoch=_log_epoch,
                    learning_rate=None,
                    rebalance_classes=False,
                    epochs=MAX_EPOCHS,
                    use_bankroll_loss=use_bankroll,
                    should_abort=cycle_aborted,
                )
            if metrics.get("samples_used", 0) > 0:
                _online_pass_count += 1
        except MemoryError as e:
            if cold_start_active:
                ensemble_engine.cancel_checkpoint_window()
                cold_start["samples_this_epoch"] = 0
                _save_cold_start(cold_start)
            add_ai_log(
                "TRAINING",
                f"Out of memory during GRU pass ({len(training_samples)} samples) — "
                f"skipping this cycle: {e}. Lower NEURALBET_COLD_START_CHUNK if this repeats.",
                level="WARNING",
            )
            metrics = {"samples_used": 0, "samples_skipped": len(training_samples)}
        except Exception as e:
            if cold_start_active:
                ensemble_engine.cancel_checkpoint_window()
                cold_start["samples_this_epoch"] = 0
                _save_cold_start(cold_start)
            add_ai_log("TRAINING", f"Training pass crashed: {e}", level="WARNING")
            raise

        if metrics.get("checkpoint_reject_reason") == "aborted" or cycle_aborted():
            add_ai_log("SYSTEM", "Training pass aborted for model reset.", level="WARNING")
            return {
                "status": "aborted",
                "predictions_count": len(predictions),
                "finished_samples_trained": 0,
            }

        if metrics["samples_used"] > 0:
            accepted = metrics.get("checkpoint_accepted", True)
            # Online reject: leave trained_count alone so the same fresh rows can
            # retry under better weights. Cold-start streaming always advances
            # archive coverage (deferred chunks + epoch gate) so a reject cannot
            # trap the final chunk in an infinite loop.
            if accepted or cold_start_active:
                f_conn2 = get_finished_connection()
                f_cursor2 = f_conn2.cursor()
                _mark_trained(f_cursor2, train_keys)
                f_conn2.commit()
                release_connection(f_conn2)
                _invalidate_archive_coverage()
            if cold_start_active:
                cold_start = _advance_cold_start(
                    cold_start, metrics["samples_used"]
                )
            if not accepted:
                gru_pass_rejected = True
                if cold_start_active:
                    add_ai_log(
                        "TRAINING",
                        "Cold-start epoch checkpoint rolled back — epoch-start weights "
                        "restored; archive progress advanced to avoid repeating a chunk.",
                        level="WARNING",
                    )
                else:
                    add_ai_log(
                        "TRAINING",
                        "Checkpoint rolled back — weights unchanged; rows left unmarked "
                        "so the next accepted pass can retrain them.",
                        level="WARNING",
                    )

            if not cold_start_active:
                if metrics.get("best_epoch", 0) <= LOW_EPOCH_ALERT_THRESHOLD:
                    _low_epoch_streak += 1
                else:
                    _low_epoch_streak = 0

            # Only chart/log a completed online pass or a finished cold-start epoch.
            # Intermediate streaming chunks have no val metrics yet.
            cold_start_epoch_done = cold_start_active and epoch_complete
            if not cold_start_active or cold_start_epoch_done:
                pin_age = _val_pin_age_seconds()
                record_training_run({
                    "generated_at": now_moscow().strftime("%Y-%m-%d %H:%M:%S"),
                    "samples_used": metrics["samples_used"],
                    "samples_skipped": metrics["samples_skipped"],
                    "positive_count": metrics["positive_count"],
                    "negative_count": metrics["negative_count"],
                    "best_epoch": metrics.get("best_epoch"),
                    "epochs_run": metrics.get("epochs_run"),
                    "train_loss": metrics["final_loss"],
                    "train_guess_rate": metrics.get("train_guess_rate"),
                    "val_loss": metrics.get("val_loss"),
                    "val_guess_rate": metrics.get("val_guess_rate"),
                    "checkpoint_accepted": (
                        None
                        if metrics.get("checkpoint_deferred")
                        else metrics.get("checkpoint_accepted")
                    ),
                    "checkpoint_saved": metrics.get("checkpoint_saved", False),
                    "checkpoint_deferred": metrics.get("checkpoint_deferred", False),
                    "val_loss_incoming": metrics.get("val_loss_incoming"),
                    "val_loss_attempted": metrics.get("val_loss_attempted"),
                    "checkpoint_reject_reason": metrics.get("checkpoint_reject_reason"),
                    "cold_start": cold_start_active,
                    "class_mix": class_mix,
                    "val_samples": len(val_samples) if val_samples else 0,
                    "val_event_count": len(_pinned_val_event_ids or ()),
                    "val_pin_age_s": round(pin_age, 1) if pin_age is not None else None,
                })

            if not cold_start_active:
                from app.neuralbet.training_history import get_training_history

                _checkpoint_reject_streak = _checkpoint_reject_streak_from_history(
                    get_training_history()
                )

            if cold_start_active and not cold_start_epoch_done:
                progress = seen + metrics["samples_used"]
                add_ai_log(
                    "TRAINING",
                    f"Cold-start chunk: {metrics['samples_used']} samples "
                    f"({metrics['positive_count']} win / {metrics['negative_count']} loss) — "
                    f"pass_loss {metrics.get('pass_loss', metrics['final_loss']):.4f}. "
                    f"Progress {progress}/{pool}. "
                    f"Checkpoint deferred to epoch end.",
                )
            else:
                pass_val = metrics.get("val_loss_attempted")
                if pass_val is None:
                    pass_val = metrics.get("val_loss")
                val_str = (
                    f", val_brier {pass_val:.4f} / val_hit_rate {metrics['val_guess_rate']:.1f}%"
                    if pass_val is not None and metrics.get("val_guess_rate") is not None
                    else (
                        f", val_brier {pass_val:.4f}"
                        if pass_val is not None
                        else ""
                    )
                )
                bank = metrics.get("bankroll") or {}
                bank_label = "Val bankroll" if bank.get("on_val") else "Training bankroll"
                turnover = bank.get("turnover_roi")
                bank_str = ""
                if bank:
                    bank_str = (
                        f". {bank_label}: {bank.get('start', 0):.1f} → {bank.get('end', 0):.1f} ₽"
                        + (f" (turnover ROI {turnover:+.1f}%)" if turnover is not None else "")
                    )
                in_sample_end = bank.get("in_sample_end")
                if (
                    bank.get("on_val")
                    and in_sample_end is not None
                    and in_sample_end > max(float(bank.get("end") or 0), 1.0) * 2
                ):
                    bank_str += (
                        f"; in-sample compound {bank.get('start', 0):.0f} → {in_sample_end:.0f} ₽ "
                        "(overfit, not counted)"
                    )
                if metrics.get("checkpoint_accepted", True):
                    checkpoint_str = (
                        ". Checkpoint saved."
                        if metrics.get("checkpoint_saved")
                        else (
                            ". Streaming epoch complete; checkpoint deferred."
                            if metrics.get("checkpoint_deferred")
                            else "."
                        )
                    )
                else:
                    checkpoint_str = (
                        f". Checkpoint rejected ({metrics.get('checkpoint_reject_reason')}): "
                        f"attempted val_brier {metrics.get('val_loss_attempted')}, "
                        f"incoming {metrics.get('val_loss_incoming')}; weights restored."
                    )
                epoch_bit = ""
                if cold_start_active:
                    epoch_bit = f"epoch {cs_epoch}/{cold_start.get('epochs_total', COLD_START_EPOCHS)}, "
                elif metrics.get("best_epoch") is not None:
                    epoch_bit = (
                        f"best epoch {metrics['best_epoch']}/{metrics['epochs_run']}, "
                    )
                add_ai_log(
                    "TRAINING",
                    (
                        "Cold-start epoch complete: "
                        if cold_start_epoch_done
                        else "Training step complete: "
                    )
                    + f"{metrics['samples_used']} samples "
                    f"({metrics['positive_count']} win / {metrics['negative_count']} loss"
                    + (
                        f", {metrics['samples_skipped']} skipped"
                        if metrics["samples_skipped"]
                        else ""
                    )
                    + f") — {epoch_bit}"
                    f"train_loss {metrics['final_loss']:.4f}"
                    + (
                        f" (hit_rate {metrics['train_guess_rate']:.1f}%)"
                        if metrics.get("train_guess_rate") is not None
                        else ""
                    )
                    + val_str
                    + bank_str
                    + (
                        f" ({bank['ruin_events']} ruin(s) this pass)"
                        if bank.get("ruin_events")
                        else ""
                    )
                    + checkpoint_str,
                )

            if _low_epoch_streak >= LOW_EPOCH_STREAK_ALERT:
                add_ai_log(
                    "TRAINING",
                    f"Possible overfitting: best_epoch <= {LOW_EPOCH_ALERT_THRESHOLD} for "
                    f"{_low_epoch_streak} consecutive pass(es) on batches ≥ {MIN_TRAIN_SAMPLES} "
                    "samples — the net memorizes each fresh batch in 1–2 epochs instead of "
                    "generalizing. Check the backtest trend; consider raising "
                    "MIN_TRAIN_SAMPLES further or temporarily disabling training.",
                    level="WARNING",
                )
            if _checkpoint_reject_streak >= CHECKPOINT_REJECT_STREAK_ALERT:
                add_ai_log(
                    "TRAINING",
                    f"Model frozen: checkpoint rejected {_checkpoint_reject_streak} "
                    f"pass(es) in a row (threshold {CHECKPOINT_REJECT_STREAK_ALERT}) — "
                    f"weights are not updating; GRU on cooldown, probe every "
                    f"{CHECKPOINT_REJECT_PROBE_EVERY_CYCLES} cycle(s). "
                    "Check val_loss incoming vs attempted in logs; a neural-net reset "
                    "may be needed.",
                    level="WARNING",
                )
        else:
            add_ai_log(
                "TRAINING",
                f"No usable samples in this batch ({metrics['samples_skipped']} skipped — unresolved outcomes). "
                "Skipped gradient step.",
                level="WARNING",
            )
    elif insufficient_for_training:
        pass  # already logged above, with the actual count — don't also claim "no matches"
    elif is_train_cycle and not gru_frozen:
        add_ai_log(
            "TRAINING", "No new finished matches in database for retraining step."
        )
    elif gru_frozen:
        # Freeze warning already logged on train cycles; avoid the false
        # "chewing the untrained backlog" message while GRU cannot advance coverage.
        if coverage.get("catch_up") and not is_train_cycle:
            add_ai_log(
                "TRAINING",
                f"Catch-up blocked by checkpoint reject streak "
                f"({_checkpoint_reject_streak}/{CHECKPOINT_REJECT_STREAK_ALERT}) — "
                f"{coverage['untrained']} unseen remain; GRU probe every "
                f"{CHECKPOINT_REJECT_PROBE_EVERY_CYCLES} cycles "
                f"(archive {coverage['trained_ratio']:.0%} trained).",
                level="WARNING",
            )
    else:
        if coverage.get("catch_up"):
            add_ai_log(
                "TRAINING",
                f"Online training skipped this cycle (catch-up every {train_every} cycles, "
                f"archive {coverage['trained_ratio']:.0%} trained, "
                f"{coverage['untrained']} unseen) — chewing the untrained backlog.",
            )
        else:
            add_ai_log(
                "TRAINING",
                f"Online training skipped this cycle (runs every {train_every} cycles) — "
                "fresh resolved bets keep accumulating for the next pass.",
            )

    if cycle_aborted():
        add_ai_log("SYSTEM", "AI cycle aborted before LightGBM/tune.", level="WARNING")
        return {
            "status": "aborted",
            "predictions_count": len(predictions),
            "finished_samples_trained": 0,
        }

    if lgb_rows:
        newest_finished = None
        for sample in lgb_rows:
            ts = sample.get("finished_at")
            if ts is None:
                continue
            if newest_finished is None or str(ts) > str(newest_finished):
                newest_finished = ts
        lgb_metrics = ensemble_engine.train_lightgbm(
            lgb_rows, val_rows=lgb_val_rows, newest_finished_at=str(newest_finished) if newest_finished else None,
        )
        if lgb_metrics.get("trained") and lgb_metrics.get("accepted"):
            top_features = sorted(
                lgb_metrics["feature_importance"].items(),
                key=lambda kv: kv[1],
                reverse=True,
            )[:3]
            top_features_str = ", ".join(
                f"{name}={score}" for name, score in top_features
            )
            add_ai_log(
                "TRAINING",
                f"LightGBM refit accepted on {lgb_metrics['samples']} resolved bets — "
                f"{lgb_metrics['eval_split']}_accuracy {lgb_metrics['train_accuracy']:.1f}%, "
                f"val Brier {lgb_metrics.get('val_brier')} "
                f"(was {lgb_metrics.get('previous_val_brier')}). Top features: {top_features_str}.",
            )
        elif lgb_metrics.get("trained"):
            add_ai_log(
                "TRAINING",
                "LightGBM candidate rejected — live booster kept. "
                + str(lgb_metrics.get("reject_reason") or "val Brier worse"),
                level="WARNING",
            )
        else:
            add_ai_log(
                "TRAINING",
                f"LightGBM refit skipped — only {lgb_metrics.get('samples', 0)} resolved bets available (need 50+).",
                level="WARNING",
            )

    if is_tune_cycle and val_samples:
        tune_metrics = ensemble_engine.tune_ensemble(
            val_samples,
            blend_market_frozen=gru_pass_rejected or gru_frozen,
        )
        if tune_metrics.get("tuned"):
            bw = tune_metrics["blend_weight"]
            mw = tune_metrics["market_weight"]
            dt = tune_metrics["decision_threshold"]
            if dt.get("skipped"):
                dt_str = (
                    f"decision_threshold frozen at {dt['new']} "
                    "(Objective B / DECISION_LOSS_WEIGHT=0; EV policy ignores residual cutoff)"
                )
            else:
                dt_str = (
                    f"decision_threshold {dt['old']} → {dt['new']} (target {dt['target']}, "
                    f"val ROI {dt['val_roi_pct']}% on {dt['val_bets']} bets)"
                    if dt["val_roi_pct"] is not None
                    else f"decision_threshold kept at {dt['old']} (no candidate cleared {ensemble_engine._MIN_THRESHOLD_BETS} val bets)"
                )
            # val_brier vs val_brier_base is the headline "is the model worth anything"
            # number: if the blended probability's Brier is >= the bare bookmaker-implied
            # probability's, the model is adding noise, not signal, on this validation
            # split, and market_weight should (and, on the next tune, will) climb.
            brier_vs_base = (
                "model beats market"
                if bw["val_brier"] < tune_metrics["val_brier_base"]
                else "market beats model"
            )
            # Only sports that actually cleared MIN_THRESHOLD_BETS_PER_SPORT this pass
            # show up in sport_decision_threshold — most passes, that's just the couple
            # of largest sports (see tune_ensemble's docstring), so this stays short
            # rather than listing 12+ "no change" entries every cycle.
            sport_thresholds = tune_metrics.get("sport_decision_threshold") or {}
            sport_str = (
                "; by sport — " + ", ".join(
                    f"{sport} {v['old']} → {v['new']} ({v['val_bets']} bets)"
                    for sport, v in sport_thresholds.items()
                )
                if sport_thresholds
                else ""
            )
            if tune_metrics.get("persisted"):
                persisted = "saved"
            elif not tune_metrics.get("accepted"):
                persisted = "unchanged (blend did not beat live + market Brier)"
            else:
                persisted = "not saved (no GRU checkpoint yet)"
            if tune_metrics.get("blend_market_frozen"):
                blend_str = (
                    f"blend/market frozen (GRU not committed), kept "
                    f"{bw['old']}/{mw['old']}"
                )
            elif not tune_metrics.get("accepted"):
                blend_str = (
                    f"blend rejected (val Brier {bw['val_brier']} ≥ live "
                    f"{tune_metrics.get('val_brier_incoming', bw['val_brier'])} or market "
                    f"{tune_metrics['val_brier_base']}, kept {bw['old']}/{mw['old']})"
                )
            else:
                blend_str = (
                    f"blend_weight {bw['old']} → {bw['new']} (target {bw['target']}), "
                    f"market_weight {mw['old']} → {mw['new']} (target {mw['target']})"
                )
            add_ai_log(
                "TRAINING",
                f"Ensemble tuned on {tune_metrics['samples']} val samples — "
                f"{blend_str} — "
                f"val Brier {bw['val_brier']} vs live {tune_metrics.get('val_brier_incoming', '—')} "
                f"vs market-only {tune_metrics['val_brier_base']} ({brier_vs_base}), "
                f"{dt_str}{sport_str}. Ensemble weights {persisted}.",
            )
            _persist_last_tune(tune_metrics)

    try:
        get_training_health()
    except Exception as e:
        logger.error(f"Error refreshing training health snapshot: {e}")

    return {
        "predictions_count": len(predictions),
        "finished_samples_trained": len(training_samples),
    }
