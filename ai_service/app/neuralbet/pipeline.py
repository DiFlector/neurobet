import json
import logging
import os
import re
import threading
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta

import torch

from app.core.database import get_connection, get_finished_connection, save_ai_predictions, release_connection
from app.neuralbet.model import NeuralBetEnsemble
from app.neuralbet import bankroll
from app.neuralbet.calibration import get_calibration_buckets, calibrate_probability
from app.neuralbet.context import overround_group_key, OVERROUND_EXPECTED_SIZE

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

AI_SETTINGS = {
    "ai_enabled": True,
    "training_enabled": True
}

# Replay-buffer knobs for the online trainer (see B4 in the plan): a resolved bet is
# eligible as "fresh" until it's been trained on MAX_REPLAY times, after which it can
# still be sampled as part of the older 30% replay slice but no longer prioritized.
# Bumped 300 -> 3000 (12 CPU cores / 14GB RAM, <5% utilized at the old size — see the
# neurobets bank/training conversation) now that backend's scrape trigger no longer
# blocks on this (see backend/main.py's trigger_ai_pipeline) — a slower training pass
# no longer risks eating the next 15s scrape cycle, so there's no reason left to leave
# this hardware idle. With ~150k+ resolved bets backlogged, 2100 fresh/cycle clears it
# in a reasonable number of cycles instead of trickling through 210/cycle.
TRAIN_BATCH_TOTAL = 3000
TRAIN_FRESH_SHARE = 0.7
MAX_REPLAY = 5
VAL_MIN_POOL = 50
VAL_FRACTION = 0.2

# LightGBM is a full batch refit (not incremental) — refitting it from scratch every
# cycle is wasted work once the dataset is large. Refit every Nth cycle instead.
# LGB_TRAIN_LIMIT raised alongside TRAIN_BATCH_TOTAL — GBDT training is cheap even at
# tens of thousands of rows with this small a feature count, and a bigger sample gives
# LightGBM a much more representative picture than 5000 rows out of 150k+.
LGB_REFIT_EVERY_CYCLES = 5
LGB_TRAIN_LIMIT = 20000

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
TRAIN_EVERY_CYCLES = int(os.getenv("NEURALBET_TRAIN_EVERY_CYCLES", "3"))
TUNE_EVERY_CYCLES = int(os.getenv("NEURALBET_TUNE_EVERY_CYCLES", "5"))

# Floor on how many samples (fresh + replay combined — see _fetch_training_batch) a
# training cycle needs before it's allowed to actually run a gradient step. Below this,
# a pass is essentially the "82 samples, best epoch 1/11, val_loss rising from the very
# first epoch" pattern seen in production logs — too few mini-batches (BATCH_SIZE=128)
# for early stopping to have anything meaningful to select between; it just memorizes
# whatever tiny slice showed up. Rows fetched on a too-small cycle are deliberately left
# with trained_count untouched (see the skip branch below) so they aren't wasted on a
# useless step — they simply get included again, alongside whatever's newly finished, on
# the next training cycle that clears this floor.
MIN_TRAIN_SAMPLES = int(os.getenv("NEURALBET_MIN_TRAIN_SAMPLES", "500"))

# Live bets are capped to this coefficient — the stats export showed ROI strictly
# worsening as coefficient rises (-3% at 1.0-1.5 down to -44% at 10.0+) while the
# ensemble's Brier score is worse than the bare bookmaker-implied probability at every
# band, meaning the model has the least information exactly where a naive EV
# calculation makes the biggest bets look most attractive. Caps real money to the range
# where the model is closest to actually knowing something, independent of how the
# probability/EV pipeline gets fixed above this.
MAX_BET_COEFF = float(os.getenv("NEURALBET_MAX_BET_COEFF", "3.0"))

AI_LOGS: List[Dict[str, Any]] = []
MAX_LOG_ENTRIES = 300

_cycle_count = 0

# "Is training helping or hurting?" tracking — see get_training_health()'s docstring for
# the full three-signal playbook this feeds. best_epoch == 1-2 on a single pass is
# normal noise; a *streak* of them on batches that already cleared MIN_TRAIN_SAMPLES is
# the real tell (the network is memorizing each fresh batch in one or two epochs instead
# of generalizing — early stopping is doing its job by bailing out fast, but that itself
# is the symptom). Counts only real training passes (skipped-for-too-few-samples cycles
# don't touch this), and resets on reset_neural_network() since a wiped model starting
# over shouldn't inherit its predecessor's bad streak.
LOW_EPOCH_ALERT_THRESHOLD = int(os.getenv("NEURALBET_LOW_EPOCH_ALERT_THRESHOLD", "2"))
LOW_EPOCH_STREAK_ALERT = int(os.getenv("NEURALBET_LOW_EPOCH_STREAK_ALERT", "3"))
_low_epoch_streak = 0

def add_ai_log(category: str, message: str, level: str = "INFO"):
    timestamp_str = now_moscow().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "timestamp": timestamp_str,
        "category": category,
        "level": level,
        "message": message
    }
    AI_LOGS.insert(0, entry)
    if len(AI_LOGS) > MAX_LOG_ENTRIES:
        AI_LOGS.pop()
    logger.info(f"[{category}] {message}")

add_ai_log("SYSTEM", "Standalone AI Microservice initialized with PyTorch, LightGBM & DeepSeek Web WASM engine.")

def get_ai_settings() -> Dict[str, Any]:
    return AI_SETTINGS


def reset_neural_network() -> Dict[str, Any]:
    """
    Admin-triggered "Обнулить нейросеть": wipes the live model (fresh random PyTorch
    weights, no LightGBM booster, blend/market weight & decision threshold back to
    defaults — see NeuralBetEnsemble.reset) and clears trained_count on every resolved
    bet, WITHOUT deleting finished_bets/finished_events themselves. That distinction is
    the whole point of this being separate from reset-db/all: the archive of resolved
    matches is normally the expensive, slow-to-rebuild part (weeks of live scraping) —
    this lets training start over from scratch while immediately having that entire
    existing archive available again as "fresh" data, instead of needing weeks of new
    matches to accumulate before the next training cycle has anything to learn from.
    Runs under _engine_lock like every other mutation of ensemble_engine, and resets
    _cycle_count to 0 so the very next inference cycle is treated as cycle 1 — which
    forces an immediate LightGBM refit + training pass + ensemble tune (see the
    is_train_cycle/is_tune_cycle/is_lgb_cycle "cycle == 1" cold-start overrides below)
    instead of waiting out TRAIN_EVERY_CYCLES/TUNE_EVERY_CYCLES/LGB_REFIT_EVERY_CYCLES
    against a model that just started over.
    """
    global _cycle_count, _low_epoch_streak
    with _engine_lock:
        ensemble_engine.reset()

        f_conn = get_finished_connection()
        f_cursor = f_conn.cursor()
        f_cursor.execute("UPDATE finished_bets SET trained_count = 0 WHERE trained_count != 0")
        reset_rows = f_cursor.rowcount
        f_conn.commit()
        release_connection(f_conn)

        _cycle_count = 0
        _low_epoch_streak = 0

    add_ai_log(
        "SYSTEM",
        f"Neural network reset by admin: PyTorch weights reinitialized, LightGBM booster "
        f"discarded, blend/market weight & decision threshold back to defaults, checkpoint "
        f"files removed, trained_count cleared on {reset_rows} resolved bet(s) — training "
        f"will restart from scratch using the existing archive.",
        level="WARNING",
    )
    return {"reset_rows": reset_rows}

def update_ai_settings(ai_enabled: Optional[bool] = None, training_enabled: Optional[bool] = None) -> Dict[str, Any]:
    if ai_enabled is not None:
        AI_SETTINGS["ai_enabled"] = ai_enabled
        status_str = "ENABLED" if ai_enabled else "DISABLED"
        add_ai_log("SYSTEM", f"AI Inference toggle changed: {status_str}")
    if training_enabled is not None:
        AI_SETTINGS["training_enabled"] = training_enabled
        status_str = "ENABLED" if training_enabled else "DISABLED"
        add_ai_log("SYSTEM", f"Online Training toggle changed: {status_str}")
    return AI_SETTINGS

def get_ai_logs() -> List[Dict[str, Any]]:
    return AI_LOGS


# Backtest-trend thresholds for get_training_health()'s signals B/C — how many of the
# most recent backtest runs (see backtest.py) to look at. 3 matches the "не улучшается
# 3+ запуска подряд" rule the admin panel's own diagnostic playbook settled on: a single
# bad backtest is noise (the resolved-bet mix each run pulls can shift day to day), a
# streak of 3 is a trend.
TRAINING_HEALTH_BACKTEST_WINDOW = int(os.getenv("NEURALBET_TRAINING_HEALTH_BACKTEST_WINDOW", "3"))


def get_training_health() -> Dict[str, Any]:
    """
    Traffic-light read on whether online training is currently helping or hurting,
    combining the three signals from the admin's own diagnostic playbook:
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
    One active signal is "presmotret'sya" (warning); all three at once is "definite stop"
    (danger) — see run_neuralbet_inference_and_training's docstring history / the admin
    panel's status block, which renders this directly. Needs at least
    TRAINING_HEALTH_BACKTEST_WINDOW backtest runs on file for B/C to activate at all —
    with fewer, only signal A (which needs no backtest) can fire.

    Returns status "disabled" (not ok/warning/danger) whenever the admin's own
    training_enabled toggle is off: with no gradient steps running, none of the three
    signals describe anything currently happening — they'd just be stale readings from
    whenever training last ran, and showing a green/red verdict on that would imply an
    ongoing process that isn't there. Signals are still reported (as their last-known
    values) so the panel can show the numbers, just not color-coded as if they were live.
    """
    from app.neuralbet.backtest import get_backtest_history

    if not AI_SETTINGS["training_enabled"]:
        return {
            "status": "disabled",
            "signals": {
                "low_epoch_streak": {"active": False, "streak": _low_epoch_streak, "threshold": LOW_EPOCH_STREAK_ALERT},
                "backtest_brier_not_beating_market": {"active": False, "runs_checked": 0, "runs_needed": TRAINING_HEALTH_BACKTEST_WINDOW},
                "backtest_roi_not_improving": {"active": False, "runs_checked": 0, "runs_needed": TRAINING_HEALTH_BACKTEST_WINDOW},
            },
        }

    signal_a = _low_epoch_streak >= LOW_EPOCH_STREAK_ALERT

    history = get_backtest_history()  # newest first
    recent = history[:TRAINING_HEALTH_BACKTEST_WINDOW]
    have_enough_backtests = len(recent) >= TRAINING_HEALTH_BACKTEST_WINDOW

    signal_b = False
    signal_c = False
    if have_enough_backtests:
        briers = [((r.get("overall") or {}).get("current") or {}).get("brier") for r in recent]
        market_briers = [(r.get("overall") or {}).get("market_brier") for r in recent]
        if all(b is not None for b in briers) and all(m is not None for m in market_briers):
            signal_b = all(b >= m for b, m in zip(briers, market_briers))

        rois = [((r.get("overall") or {}).get("current") or {}).get("roi_pct") for r in recent]
        if all(v is not None for v in rois):
            # recent[0] is newest, recent[-1] is oldest in this window.
            signal_c = rois[0] <= rois[-1]

    active = sum([signal_a, signal_b, signal_c])
    status = "danger" if active >= 3 else "warning" if active >= 1 else "ok"

    return {
        "status": status,
        "signals": {
            "low_epoch_streak": {
                "active": signal_a, "streak": _low_epoch_streak, "threshold": LOW_EPOCH_STREAK_ALERT,
            },
            "backtest_brier_not_beating_market": {
                "active": signal_b, "runs_checked": len(recent), "runs_needed": TRAINING_HEALTH_BACKTEST_WINDOW,
            },
            "backtest_roi_not_improving": {
                "active": signal_c, "runs_checked": len(recent), "runs_needed": TRAINING_HEALTH_BACKTEST_WINDOW,
            },
        },
    }


def _parse_score_diff(score_at_time: Optional[str]) -> int:
    try:
        a, b = str(score_at_time or "0:0").split(":", 1)
        return int(a) - int(b)
    except Exception:
        return 0


def _parse_ts_epoch(raw: Any) -> Optional[float]:
    """odds_history.timestamp ("YYYY-MM-DD HH:MM:SS", naive Moscow time) -> epoch
    seconds, or None if unparseable. Mirrors backend/database.py's _parse_ts_epoch —
    only ever consumed as differences between snapshots of one bet, so the naive
    timezone's absolute offset doesn't matter."""
    try:
        return datetime.fromisoformat(str(raw)).timestamp()
    except Exception:
        return None


# Mirrors backend/database.py's _parse_timer_seconds — see that function's comment for
# why unrecognized strings deliberately return None instead of a guessed number.
_TIMER_MMSS_RE = re.compile(r"^(\d{1,3}):([0-5]\d)$")
_TIMER_PLUS_RE = re.compile(r"^(\d{1,3})\+(\d{1,2})'?$")
_TIMER_MIN_RE = re.compile(r"^(\d{1,3})'$")


def _parse_timer_seconds(raw: Optional[str]) -> Optional[float]:
    s = str(raw or "").strip()
    if not s:
        return None
    m = _TIMER_MMSS_RE.match(s)
    if m:
        return float(int(m.group(1)) * 60 + int(m.group(2)))
    m = _TIMER_PLUS_RE.match(s)
    if m:
        return float((int(m.group(1)) + int(m.group(2))) * 60)
    m = _TIMER_MIN_RE.match(s)
    if m:
        return float(int(m.group(1)) * 60)
    return None


def _get_val_event_ids(f_cursor) -> Optional[set]:
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
    f_cursor.execute("SELECT COUNT(DISTINCT event_id) AS c FROM finished_bets WHERE is_win IS NOT NULL")
    total_events = f_cursor.fetchone()["c"]
    if total_events < VAL_MIN_POOL:
        return None
    val_event_count = max(int(total_events * VAL_FRACTION), 10)
    f_cursor.execute("""
        SELECT event_id FROM (
            SELECT event_id, MAX(finished_at) AS ev_finished_at
              FROM finished_bets WHERE is_win IS NOT NULL
             GROUP BY event_id
        ) t
        ORDER BY ev_finished_at DESC
        LIMIT %s
    """, (val_event_count,))
    return {r["event_id"] for r in f_cursor.fetchall()}


def _row_to_sample(r) -> Dict[str, Any]:
    try:
        odds_seq = json.loads(r["odds_seq_json"] or "[]")
    except Exception:
        odds_seq = []
    try:
        score_seq = json.loads(r["score_seq_json"] or "[]") if r["score_seq_json"] else []
    except Exception:
        score_seq = []
    try:
        ts_seq = json.loads(r["ts_seq_json"] or "[]") if ("ts_seq_json" in r.keys() and r["ts_seq_json"]) else []
    except Exception:
        ts_seq = []
    try:
        timer_seq = json.loads(r["timer_seq_json"] or "[]") if ("timer_seq_json" in r.keys() and r["timer_seq_json"]) else []
    except Exception:
        timer_seq = []
    return {
        "is_win": r["is_win"],
        "odds_seq": odds_seq,
        "score_seq": score_seq,
        "ts_seq": ts_seq,
        "timer_seq": timer_seq,
        "score_diff_at_bet": r["score_diff_at_bet"] or 0,
        "factor_id": r["factor_id"],
        "sport_path": r["sport_path"] or "",
        "team_1": r["team_1"] or "",
        "team_2": r["team_2"] or "",
        # NULL for bets archived before the overround_close migration, or for markets
        # outside the core set it's even computed for (see backend/database.py's
        # _overround_group_key) — the LightGBM feature builder treats that as "unknown"
        # (OVERROUND_UNKNOWN sentinel in model.py), not zero margin.
        "overround_close": r["overround_close"] if "overround_close" in r.keys() else None,
        "_key": (r["event_id"], r["factor_id"], r["parameter"], r["market_prefix"]),
    }


def _fetch_training_batch(f_cursor, val_event_ids: Optional[set]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Builds this cycle's training batch: ~70% never-or-rarely-trained bets (freshest
    first) + ~30% older ones sampled at random so the model doesn't catastrophically
    forget history it hasn't seen in a while (see plan B4). Excludes every bet belonging
    to a held-out validation event (see _get_val_event_ids) — event membership, not a
    finished_at threshold, so a held-out match's bets can never leak into training no
    matter when each of its markets settled. Returns (rows, key_list) where key_list is
    the (event_id, factor_id, parameter, market_prefix) tuples to bump trained_count for.
    """
    where_val = "AND h.event_id != ALL(%s)" if val_event_ids else ""
    params_val = [list(val_event_ids)] if val_event_ids else []

    fresh_n = int(TRAIN_BATCH_TOTAL * TRAIN_FRESH_SHARE)
    f_cursor.execute(f"""
        SELECT h.event_id, h.factor_id, h.parameter, h.market_prefix, h.is_win,
               h.odds_seq_json, h.score_seq_json, h.ts_seq_json, h.timer_seq_json, h.score_diff_at_bet, h.finished_at, h.overround_close,
               f.sport_path, f.team_1, f.team_2
        FROM finished_bets h
        JOIN finished_events f ON h.event_id = f.event_id
        WHERE h.is_win IS NOT NULL AND h.trained_count = 0 {where_val}
        ORDER BY h.finished_at DESC
        LIMIT %s
    """, params_val + [fresh_n])
    fresh_rows = f_cursor.fetchall()

    replay_n = TRAIN_BATCH_TOTAL - len(fresh_rows)
    replay_rows = []
    if replay_n > 0:
        f_cursor.execute(f"""
            SELECT h.event_id, h.factor_id, h.parameter, h.market_prefix, h.is_win,
                   h.odds_seq_json, h.score_seq_json, h.ts_seq_json, h.timer_seq_json, h.score_diff_at_bet, h.finished_at, h.overround_close,
                   f.sport_path, f.team_1, f.team_2
            FROM finished_bets h
            JOIN finished_events f ON h.event_id = f.event_id
            WHERE h.is_win IS NOT NULL AND h.trained_count BETWEEN 1 AND %s {where_val}
            ORDER BY RANDOM()
            LIMIT %s
        """, [MAX_REPLAY - 1] + params_val + [replay_n])
        replay_rows = f_cursor.fetchall()

    all_rows = list(fresh_rows) + list(replay_rows)
    samples = [_row_to_sample(r) for r in all_rows]
    keys = [s["_key"] for s in samples]
    return samples, keys


def _fetch_val_batch(f_cursor, val_event_ids: Optional[set]) -> List[Dict[str, Any]]:
    if not val_event_ids:
        return []
    f_cursor.execute("""
        SELECT h.event_id, h.factor_id, h.parameter, h.market_prefix, h.is_win,
               h.odds_seq_json, h.score_seq_json, h.ts_seq_json, h.timer_seq_json, h.score_diff_at_bet, h.finished_at, h.overround_close,
               f.sport_path, f.team_1, f.team_2
        FROM finished_bets h
        JOIN finished_events f ON h.event_id = f.event_id
        WHERE h.is_win IS NOT NULL AND h.event_id = ANY(%s)
        ORDER BY h.finished_at ASC
        LIMIT 400
    """, (list(val_event_ids),))
    return [_row_to_sample(r) for r in f_cursor.fetchall()]


def _mark_trained(f_cursor, keys: List[tuple]):
    for eid, fid, param, prefix in keys:
        f_cursor.execute("""
            UPDATE finished_bets SET trained_count = trained_count + 1
            WHERE event_id = %s AND factor_id = %s AND COALESCE(parameter,'') = COALESCE(%s,'')
              AND COALESCE(market_prefix,'') = COALESCE(%s,'')
        """, (eid, fid, param, prefix))


# Settlement of live_bets now happens in backend (database.settle_live_bets), fired the
# moment an event is archived there — see backend/database.py:save_parsed_events. This
# service no longer holds an open connection to autobet_finished.db for the "live"
# account at all; it only proposes candidates and reads its balance back over HTTP
# (see bankroll.fetch_live_balance / bankroll.submit_live_bet_candidates).


def _place_live_bets(candidates: List[Dict[str, Any]]):
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
        return {"placed": 0, "reason": "insufficient_available_balance", "candidates": len(candidates)}

    # Cap the candidate pool before allocating — allocate() would gladly Kelly-size all
    # of them, but only the strongest few are worth considering.
    LIVE_CANDIDATE_POOL = 20
    candidates = sorted(candidates, key=lambda c: c["expected_roi"], reverse=True)[:LIVE_CANDIDATE_POOL]
    # win_probs uses the calibrated probability (same number the "Нейроставки" UI shows
    # and the one actually stored/acted on — see calibrate_probability), not the raw
    # ensemble score, so Kelly sizing is staking against the same edge estimate as
    # everything else that reasons about this bet.
    win_probs = torch.tensor([c["win_probability"] / 100.0 for c in candidates], dtype=torch.float32)
    coeffs_t = torch.tensor([c["coeff"] for c in candidates], dtype=torch.float32)
    stake_logits = torch.tensor([c["stake_logit"] for c in candidates], dtype=torch.float32)
    with torch.no_grad():
        fractions = bankroll.allocate(win_probs, coeffs_t, stake_logits)

    # Sizing (which candidates, how much of the bank) stays a model decision made here.
    # Execution (does this market still exist, does it actually get written, at what
    # exact stake given the balance backend sees right now) belongs to backend — see
    # database.place_live_bet_candidates, which re-validates freshness before writing.
    to_submit = [
        {
            "event_id": c["event_id"], "factor_id": c["factor_id"], "market_prefix": c["market_prefix"],
            "parameter": c["parameter"], "label": c.get("label", ""), "match_name": c.get("match_name", ""),
            "coefficient": c["coeff"], "stake_fraction": frac, "win_probability": c["win_probability"],
        }
        for c, frac in zip(candidates, fractions.tolist()) if frac >= bankroll.MIN_STAKE_FRACTION
    ]

    if not to_submit:
        # allocate() found candidates but every fraction fell below the 10%-of-bank
        # floor (bankroll.MIN_STAKE_FRACTION) — usually because the stake head hasn't
        # learned to concentrate exposure on a favorite yet, so softmax spreads it too
        # thin across the candidate pool. Surface the actual numbers so this is
        # diagnosable from the admin log instead of silently doing nothing.
        best_frac = max(fractions.tolist()) if len(fractions) else 0.0
        return {
            "placed": 0, "reason": "all_fractions_below_min_stake", "candidates": len(candidates),
            "best_fraction_pct": round(best_frac * 100.0, 2),
        }

    result = bankroll.submit_live_bet_candidates(to_submit)
    return {"placed": len(result.get("placed", [])), "candidates": len(candidates), "skipped": result.get("skipped", [])}


def run_neuralbet_inference_and_training(scrape_timestamp: Optional[str] = None) -> Dict[str, Any]:
    global _cycle_count
    with _engine_lock:
        return _run_neuralbet_inference_and_training_locked(scrape_timestamp)


def _run_neuralbet_inference_and_training_locked(scrape_timestamp: Optional[str] = None) -> Dict[str, Any]:
    global _cycle_count
    _cycle_count += 1

    if not AI_SETTINGS["ai_enabled"]:
        add_ai_log("INFERENCE", "AI Inference skipped (Disabled by Admin).", level="WARNING")
        return {"status": "disabled", "predictions_count": 0, "finished_samples_trained": 0}

    conn = get_connection()
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
    # Combat sports (единоборства/MMA/UFC/бокс) are excluded from betting: Fonbet's live
    # list has a display-side lag for them that causes premature/incorrect settlement
    # (see the exclusion in parser_service.py). This is a defense-in-depth guard in case
    # a combat-sport event was already stored before the parser-level fix took effect.
    cursor.execute("""
        SELECT
            l.event_id, l.factor_id, l.market_prefix, l.label, l.parameter, l.coefficient,
            e.sport_path, e.match_name, e.score_1, e.score_2, e.timer, e.team_1, e.team_2
        FROM latest_odds l
        JOIN events e ON l.event_id = e.event_id
        WHERE e.is_live = 1
          AND l.updated_at = e.last_updated_at
          AND e.last_updated_at = %s
          AND e.sport_path !~* '(единоборства|mma|ufc|бокс)'
    """, (target_ts,))
    live_odds_rows = cursor.fetchall()

    # Pull the whole odds+score history for these events in one query and group it in
    # Python, instead of one query per row. Score is carried alongside the coefficient
    # at every step (score_at_time), not the final match score — the model must be
    # trained and served on the same kind of "as it looked live" trajectory, or the
    # LightGBM/PyTorch accuracy numbers are measuring a leak, not real skill.
    event_ids = list({row["event_id"] for row in live_odds_rows})
    # Per-step (coefficient, score_diff, ts_epoch, timer_seconds) tuples — ts_epoch/
    # timer_seconds may be None for an unparseable timestamp/timer string, in which case
    # _build_sequence falls back to its positional/unknown sentinel for that step.
    trajectory_map: Dict[tuple, List[Tuple[float, int, Optional[float], Optional[float]]]] = {}
    if event_ids:
        cursor.execute("""
            SELECT event_id, factor_id, parameter, market_prefix, coefficient, score_at_time, timestamp, timer_at_time
            FROM odds_history
            WHERE event_id = ANY(%s)
            ORDER BY id ASC
        """, (event_ids,))
        for h in cursor.fetchall():
            key = (h["event_id"], h["factor_id"], str(h["parameter"] or ""), h["market_prefix"] or "")
            trajectory_map.setdefault(key, []).append((
                float(h["coefficient"]), _parse_score_diff(h["score_at_time"]),
                _parse_ts_epoch(h["timestamp"]), _parse_timer_seconds(h["timer_at_time"]),
            ))

    # Overround (sum of 1/coeff across sibling outcomes) at the current live snapshot —
    # a LightGBM feature the raw per-outcome coefficient can't express on its own (see
    # model.py's LGB_FEATURE_NAMES / OVERROUND_UNKNOWN). Computed from latest_odds
    # (already the freshest snapshot per market, no history/timing alignment needed)
    # for the same core outcome set backend/database.py's archival path covers.
    overround_map: Dict[Tuple[Any, str, str], float] = {}
    if event_ids:
        cursor.execute("""
            SELECT event_id, factor_id, COALESCE(parameter, '') AS parameter, coefficient
            FROM latest_odds
            WHERE event_id = ANY(%s)
        """, (event_ids,))
        grouped: Dict[Tuple[Any, str, str], List[float]] = {}
        for r in cursor.fetchall():
            key = overround_group_key(r["factor_id"], r["parameter"])
            if key is None:
                continue
            coeff = r["coefficient"]
            if coeff and coeff > 1.0:
                grouped.setdefault((r["event_id"], key[0], key[1]), []).append(float(coeff))
        for (oeid, group, oparam), coeffs in grouped.items():
            if len(coeffs) >= OVERROUND_EXPECTED_SIZE[group]:
                overround_map[(oeid, group, oparam)] = sum(1.0 / c for c in coeffs)

    batch_items = []
    row_meta = []
    for row in live_odds_rows:
        eid = row["event_id"]
        fid = row["factor_id"]
        prefix = row["market_prefix"] or ""
        param = str(row["parameter"] or "")
        coeff = float(row["coefficient"] or 1.0)
        s1 = int(row["score_1"] or 0)
        s2 = int(row["score_2"] or 0)

        # No odds_history rows found for this market (freshly appeared this cycle) —
        # fall back to a single live snapshot, still carrying the event's *current*
        # timer (e.timer, otherwise unused) so even a brand-new market isn't missing
        # the match-time feature entirely.
        step_pairs = trajectory_map.get((eid, fid, param, prefix)) or [
            (coeff, s1 - s2, None, _parse_timer_seconds(row["timer"]))
        ]
        initial_coeff = step_pairs[0][0] if step_pairs else coeff

        ov_key = overround_group_key(fid, param)
        overround = overround_map.get((eid, ov_key[0], ov_key[1])) if ov_key else None

        batch_items.append({
            "step_pairs": step_pairs,
            "current_coeff": coeff,
            "initial_coeff": initial_coeff,
            "factor_id": fid,
            "sport_path": row["sport_path"] or "",
            "team_1": row["team_1"] or "",
            "team_2": row["team_2"] or "",
            "overround": overround,
        })
        row_meta.append({
            "event_id": eid, "factor_id": fid, "market_prefix": prefix, "parameter": param,
            "coeff": coeff, "label": row["label"] or "", "match_name": row["match_name"] or "",
            "sport": (row["sport_path"] or "").split("/")[0].strip() or None,
        })

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

    for meta, (win_prob, error_rate, lgb_score, torch_score, decision_prob, stake_logit, exposure_logit) in zip(row_meta, batch_results):
        eid = meta["event_id"]
        fid = meta["factor_id"]
        prefix = meta["market_prefix"]
        param = meta["parameter"]
        coeff = meta["coeff"]

        calibrated_prob = calibrate_probability(win_prob, buckets, sport=meta["sport"], coeff=coeff)
        expected_roi = ((calibrated_prob / 100.0) * coeff - 1.0) * 100.0
        # The verdict: the model's own decision head, not a probability/EV cutoff picked
        # from outside — see decision_logit in OddsTrajectoryGRU. Threshold defaults to
        # 0.5 but is retuned against validation ROI periodically — see
        # ensemble_engine.tune_ensemble / the TUNING log line below.
        predicted_win = 1 if decision_prob >= ensemble_engine.decision_threshold else 0
        if predicted_win:
            predicted_win_count += 1
        else:
            predicted_loss_count += 1

        predictions.append({
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
        })

        if predicted_win and coeff <= MAX_BET_COEFF:
            live_candidates.append({
                **meta, "win_probability": round(calibrated_prob, 1), "expected_roi": expected_roi,
                "stake_logit": stake_logit,
            })

    if predictions:
        save_ai_predictions(predictions, timestamp_str)
        add_ai_log(
            "INFERENCE",
            f"Evaluated predictions for {len(predictions)} active live outcomes — "
            f"{predicted_win_count} verdict 'выиграет' / {predicted_loss_count} verdict 'проиграет'. "
            "(PyTorch & LightGBM scores saved)"
        )
    release_connection(conn)

    # --- Live bankroll: propose bets to backend, which validates freshness, executes,
    # and settles resolved ones on its own cycle (see backend/database.py) ---
    place_result = _place_live_bets(live_candidates)
    if place_result.get("placed"):
        skipped_stale = sum(1 for s in place_result.get("skipped", []) if s.get("reason") == "stale_market")
        extra = f" ({skipped_stale} пропущено как устаревшие)" if skipped_stale else ""
        add_ai_log("BANKROLL", f"Live bankroll: opened {place_result['placed']} new bet(s) this cycle.{extra}")
    else:
        reason = place_result.get("reason")
        if reason == "no_predicted_win_candidates":
            add_ai_log("BANKROLL", "Live bankroll: 0 bets — сеть не считает ни один исход выигрышным в этом цикле.")
        elif reason == "insufficient_available_balance":
            add_ai_log("BANKROLL", "Live bankroll: 0 bets — available balance is 0 (all locked or ruined).", level="WARNING")
        elif reason == "all_fractions_below_min_stake":
            add_ai_log(
                "BANKROLL",
                f"Live bankroll: 0 bets — {place_result['candidates']} predicted-win candidate(s) found, "
                f"but the allocator's best fraction was only {place_result['best_fraction_pct']}% "
                f"of bank (needs >= {bankroll.MIN_STAKE_FRACTION * 100:.0f}%). "
                "Stake head hasn't concentrated exposure on a favorite yet — expected while undertrained.",
                level="WARNING",
            )

    if not AI_SETTINGS["training_enabled"]:
        add_ai_log("TRAINING", "Online Retraining skipped (Disabled by Admin).", level="WARNING")
        return {"predictions_count": len(predictions), "finished_samples_trained": 0}

    # Force cycle 1 too (cold start — otherwise every one of these stays at its
    # untrained/untuned default for the first TRAIN_EVERY_CYCLES/TUNE_EVERY_CYCLES/
    # LGB_REFIT_EVERY_CYCLES cycles after every restart).
    is_train_cycle = _cycle_count == 1 or _cycle_count % TRAIN_EVERY_CYCLES == 0
    is_tune_cycle = _cycle_count == 1 or _cycle_count % TUNE_EVERY_CYCLES == 0
    is_lgb_cycle = _cycle_count == 1 or _cycle_count % LGB_REFIT_EVERY_CYCLES == 0

    training_samples: List[Dict[str, Any]] = []
    val_samples: List[Dict[str, Any]] = []
    train_keys: List[tuple] = []
    lgb_rows: List[Dict[str, Any]] = []
    lgb_val_rows: List[Dict[str, Any]] = []
    try:
        f_conn = get_finished_connection()
        f_cursor = f_conn.cursor()

        val_event_ids = _get_val_event_ids(f_cursor)
        # Skipping the fetch on a non-training cycle isn't just "do less work" — it's
        # what makes the accumulation in TRAIN_EVERY_CYCLES's docstring actually happen:
        # matches that finish in between stay trained_count = 0 (see _mark_trained,
        # only called after a training cycle) until the next training cycle picks them
        # all up together as one larger batch.
        if is_train_cycle:
            training_samples, train_keys = _fetch_training_batch(f_cursor, val_event_ids)
        if is_train_cycle or is_tune_cycle or is_lgb_cycle:
            val_samples = _fetch_val_batch(f_cursor, val_event_ids)

        # LightGBM feature rows use the coefficient/score as they stood at the point in
        # the trajectory used for that sample (the last odds_seq/score_seq entry) — same
        # leak fix as the GRU path, not the final match state.
        def to_lgb_rows(samples):
            out = []
            for s in samples:
                odds_seq = s["odds_seq"]
                score_seq = s["score_seq"] or [s["score_diff_at_bet"]] * len(odds_seq)
                if not odds_seq:
                    continue
                out.append({
                    "factor_id": s["factor_id"],
                    "is_win": s["is_win"],
                    "coefficient": odds_seq[-1],
                    "initial_coefficient": odds_seq[0],
                    "min_coefficient": min(odds_seq),
                    "max_coefficient": max(odds_seq),
                    "samples_count": len(odds_seq),
                    "score_diff": score_seq[-1] if score_seq else 0,
                    "sport_path": s["sport_path"],
                    "team_1": s.get("team_1", ""),
                    "team_2": s.get("team_2", ""),
                    "overround_close": s.get("overround_close"),
                })
            return out

        if is_lgb_cycle:
            f_cursor.execute("""
                SELECT h.event_id, h.factor_id, h.parameter, h.market_prefix, h.is_win,
                       h.odds_seq_json, h.score_seq_json, h.ts_seq_json, h.timer_seq_json, h.score_diff_at_bet, h.finished_at, h.overround_close,
                       f.sport_path, f.team_1, f.team_2
                FROM finished_bets h
                JOIN finished_events f ON h.event_id = f.event_id
                WHERE h.is_win IS NOT NULL AND (%s::bigint[] IS NULL OR h.event_id != ALL(%s))
                ORDER BY h.finished_at DESC
                LIMIT %s
            """, (list(val_event_ids) if val_event_ids else None, list(val_event_ids) if val_event_ids else [], LGB_TRAIN_LIMIT))
            lgb_rows = to_lgb_rows([_row_to_sample(r) for r in f_cursor.fetchall()])
            lgb_val_rows = to_lgb_rows(val_samples)

        release_connection(f_conn)
    except Exception as e:
        logger.error(f"Error querying finished training db: {e}")

    fetched_train_count = len(training_samples)
    insufficient_for_training = training_samples and fetched_train_count < MIN_TRAIN_SAMPLES
    if insufficient_for_training:
        add_ai_log(
            "TRAINING",
            f"Skipping training step — only {fetched_train_count} samples available "
            f"(need {MIN_TRAIN_SAMPLES}+; below that, one epoch is enough to memorize the "
            "whole batch instead of learning anything general). Rows stay unmarked and "
            "will be retried, with whatever's newly finished added in, next training cycle.",
            level="WARNING",
        )
        training_samples = []

    if training_samples:
        add_ai_log(
            "TRAINING",
            f"Starting online training pass: {len(training_samples)} samples "
            f"({int(len(training_samples) * TRAIN_FRESH_SHARE)} fresh target / rest replay), "
            f"{len(val_samples)} held out for validation."
        )

        def _log_epoch(epoch_idx: int, train_loss: float, val_loss: Optional[float]):
            if val_loss is not None:
                add_ai_log("TRAINING", f"Epoch {epoch_idx} — train_loss: {train_loss:.4f}, val_loss: {val_loss:.4f}")
            else:
                add_ai_log("TRAINING", f"Epoch {epoch_idx} — train_loss: {train_loss:.4f}")

        metrics = ensemble_engine.train_online(training_samples, val_data=val_samples, on_epoch=_log_epoch)

        if metrics["samples_used"] > 0:
            f_conn2 = get_finished_connection()
            f_cursor2 = f_conn2.cursor()
            _mark_trained(f_cursor2, train_keys)
            f_conn2.commit()
            release_connection(f_conn2)

            global _low_epoch_streak
            if metrics["best_epoch"] <= LOW_EPOCH_ALERT_THRESHOLD:
                _low_epoch_streak += 1
            else:
                _low_epoch_streak = 0

            val_str = (
                f", val_loss {metrics['val_loss']:.4f} / val_guess_rate {metrics['val_guess_rate']:.1f}%"
                if metrics.get("val_loss") is not None else " (no validation split yet — need more resolved bets)"
            )
            bank = metrics.get("bankroll") or {}
            add_ai_log(
                "TRAINING",
                f"Training step complete: {metrics['samples_used']} samples "
                f"({metrics['positive_count']} win / {metrics['negative_count']} loss"
                + (f", {metrics['samples_skipped']} skipped" if metrics["samples_skipped"] else "")
                + f") — best epoch {metrics['best_epoch']}/{metrics['epochs_run']}, "
                f"train_loss {metrics['final_loss']:.4f} (guess_rate {metrics['train_guess_rate']:.1f}%)"
                + val_str
                + f". Training bankroll: {bank.get('start', 0):.1f} → {bank.get('end', 0):.1f} ₽"
                + (f" ({bank['ruin_events']} ruin(s) this pass)" if bank.get("ruin_events") else "")
                + ". Checkpoint saved."
            )

            if _low_epoch_streak >= LOW_EPOCH_STREAK_ALERT:
                add_ai_log(
                    "TRAINING",
                    f"⚠️ Возможное переобучение: best_epoch <= {LOW_EPOCH_ALERT_THRESHOLD} уже "
                    f"{_low_epoch_streak} проход(ов) подряд на батчах ≥ {MIN_TRAIN_SAMPLES} сэмплов — "
                    "сеть выучивает каждый свежий батч за 1-2 эпохи вместо обобщения. "
                    "Проверьте тренд бэктеста; возможно, стоит поднять MIN_TRAIN_SAMPLES ещё выше "
                    "или временно выключить обучение.",
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
    elif is_train_cycle:
        add_ai_log("TRAINING", "No new finished matches in database for retraining step.")
    else:
        add_ai_log(
            "TRAINING",
            f"Online training skipped this cycle (runs every {TRAIN_EVERY_CYCLES} cycles) — "
            "fresh resolved bets keep accumulating for the next pass.",
        )

    if lgb_rows:
        lgb_metrics = ensemble_engine.train_lightgbm(lgb_rows, val_rows=lgb_val_rows)
        if lgb_metrics.get("trained"):
            top_features = sorted(lgb_metrics["feature_importance"].items(), key=lambda kv: kv[1], reverse=True)[:3]
            top_features_str = ", ".join(f"{name}={score}" for name, score in top_features)
            add_ai_log(
                "TRAINING",
                f"LightGBM refit on {lgb_metrics['samples']} resolved bets — "
                f"{lgb_metrics['eval_split']}_accuracy {lgb_metrics['train_accuracy']:.1f}%. Top features: {top_features_str}."
            )
        else:
            add_ai_log(
                "TRAINING",
                f"LightGBM refit skipped — only {lgb_metrics.get('samples', 0)} resolved bets available (need 50+).",
                level="WARNING",
            )

    if is_tune_cycle and val_samples:
        tune_metrics = ensemble_engine.tune_ensemble(val_samples)
        if tune_metrics.get("tuned"):
            bw = tune_metrics["blend_weight"]
            mw = tune_metrics["market_weight"]
            dt = tune_metrics["decision_threshold"]
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
                "model beats market" if bw["val_brier"] < tune_metrics["val_brier_base"] else "market beats model"
            )
            add_ai_log(
                "TRAINING",
                f"Ensemble tuned on {tune_metrics['samples']} val samples — "
                f"blend_weight {bw['old']} → {bw['new']} (target {bw['target']}), "
                f"market_weight {mw['old']} → {mw['new']} (target {mw['target']}) — "
                f"val Brier {bw['val_brier']} vs market-only {tune_metrics['val_brier_base']} ({brier_vs_base}), {dt_str}."
            )

    return {
        "predictions_count": len(predictions),
        "finished_samples_trained": len(training_samples)
    }
