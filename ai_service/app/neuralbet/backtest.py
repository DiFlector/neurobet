"""
Offline evaluation of the *current, already-trained* ensemble against historical
resolved bets — answers "if today's weights/calibration/threshold had been live back
then, would the numbers actually look better?" without waiting days for the online
trainer to accumulate enough new matches to tell.

Deliberately NOT the same thing as the online trainer's val_loss/val_guess_rate: those
measure the model against a fixed ~400-sample validation slice on a random trajectory
cutoff, refreshed with every online-training pass. This runs a much larger, deterministic
sweep (the *closing* trajectory — the full history as recorded up to settlement, not a
random cutoff), broken down by coefficient band and sport the same way the
"Статистика" page's ROI table is, and — the actually new part — recomputes each bet
with the live ensemble's current weights/blend/threshold instead of reading back
whatever was actually predicted at the time (predicted_win_probability/predicted_win,
which mixes together however many different model versions were live across the whole
history window). Read-only: no gradient step, no checkpoint write, no bankroll ledger
entry. Runs under pipeline._engine_lock like every other read of ensemble_engine, so it
can't race a concurrent train_online() pass reading torn-mid-update weights.
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.config import MODEL_DIR
from app.core.database import get_finished_connection, release_connection
from app.neuralbet.calibration import calibrate_probability, coeff_bucket_index, get_calibration_buckets

logger = logging.getLogger("ai_service_backtest")

BACKTEST_DIR = os.path.join(MODEL_DIR, "backtests")
HISTORY_PATH = os.path.join(BACKTEST_DIR, "history.json")
# 50 -> 180: now that a backtest also fires automatically 4x/day (00:00/06:00/12:00/
# 18:00 Moscow — see main.py's scheduler) on top of manual admin-panel runs, 50 entries
# would scroll off in under 2 weeks. 180 covers roughly a month and a half of
# quarter-daily runs plus headroom for manual ones, cheap either way (this is just JSON).
MAX_HISTORY_RUNS = 180

COEFF_BUCKET_LABELS = ["1.0–1.5", "1.5–2.0", "2.0–3.0", "3.0–5.0", "5.0–10.0", "10.0+"]

# predict_batch's own forward pass is cheap per-sample, but chunking bounds peak memory
# for a large --limit run instead of building one giant tensor up front.
PREDICT_CHUNK = 4000

# Same fixed +3 offset pipeline.py's now_moscow() uses — "generated_at" (and the
# filename slug derived from it, see save_and_record) is what the admin panel's
# "История запусков" table and the on-disk backtest_*.json filenames show, and both
# were showing UTC, off by 3 hours from every other timestamp in the app.
MOSCOW_TZ = timezone(timedelta(hours=3))


def now_iso() -> str:
    return datetime.now(MOSCOW_TZ).isoformat()


def _build_full_step_pairs(sample: Dict[str, Any]) -> Optional[List[tuple]]:
    """
    Same length-reconciliation model.py's _prepare_sample applies to a resolved bet's
    stored sequences (score_seq/ts_seq/timer_seq may predate the migration that added
    them, or simply be shorter/absent — see finished_bets' schema notes), but without
    _prepare_sample's random training-time cutoff: a backtest wants the deterministic
    *closing* trajectory (the full history as it stood at settlement) so re-running it
    twice against the same weights gives the same numbers.
    """
    odds_seq = sample["odds_seq"]
    if not odds_seq:
        return None
    score_seq = sample["score_seq"]
    if len(score_seq) != len(odds_seq):
        score_seq = [sample["score_diff_at_bet"]] * len(odds_seq)
    ts_seq = sample["ts_seq"]
    if len(ts_seq) != len(odds_seq):
        ts_seq = [None] * len(odds_seq)
    timer_seq = sample["timer_seq"]
    if len(timer_seq) != len(odds_seq):
        timer_seq = [None] * len(odds_seq)
    return list(zip(odds_seq, score_seq, ts_seq, timer_seq))


def _fetch_backtest_rows(limit: int, since: Optional[str]) -> List[Any]:
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()
    where_since = "AND h.finished_at >= %s" if since else ""
    params: List[Any] = [since] if since else []
    f_cursor.execute(f"""
        SELECT h.event_id, h.factor_id, h.label, h.parameter, h.market_prefix, h.is_win,
               h.odds_seq_json, h.score_seq_json, h.ts_seq_json, h.timer_seq_json,
               h.score_diff_at_bet, h.finished_at, h.overround_close,
               h.final_coefficient, h.predicted_win_probability, h.predicted_win,
               f.sport_path, f.team_1, f.team_2
        FROM finished_bets h
        JOIN finished_events f ON h.event_id = f.event_id
        WHERE h.is_win IS NOT NULL {where_since}
        ORDER BY h.finished_at DESC
        LIMIT %s
    """, params + [limit])
    rows = f_cursor.fetchall()
    release_connection(f_conn)
    return rows


def _row_to_sample(r) -> Dict[str, Any]:
    """Local copy of pipeline._row_to_sample's JSON-parsing (kept separate rather than
    imported, so this module never has to import pipeline.py — see the module docstring
    on why that direction matters: pipeline owns the live singleton and its lock, and
    importing *from* pipeline into a read-only reporting module would be backwards)."""
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
        "odds_seq": odds_seq, "score_seq": score_seq, "ts_seq": ts_seq, "timer_seq": timer_seq,
        "score_diff_at_bet": r["score_diff_at_bet"] or 0,
        "factor_id": r["factor_id"], "label": r["label"] or "", "sport_path": r["sport_path"] or "",
        "team_1": r["team_1"] or "", "team_2": r["team_2"] or "",
        "overround_close": r["overround_close"] if "overround_close" in r.keys() else None,
    }


def _agg_group(records: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    n = len(records)
    if n == 0:
        return None

    def _one(prob_key: str, pred_key: str) -> Optional[Dict[str, Any]]:
        have_prob = [r for r in records if r.get(prob_key) is not None]
        evaluated = len(have_prob)
        if evaluated == 0:
            return None
        have_pred = [r for r in have_prob if r.get(pred_key) is not None]
        guessed = sum(1 for r in have_pred if int(r[pred_key]) == r["is_win"])
        bets = [r for r in have_pred if int(r[pred_key]) == 1]
        staked = len(bets)
        returned = sum(r["coeff"] for r in bets if r["is_win"] == 1)
        brier = sum((r[prob_key] / 100.0 - r["is_win"]) ** 2 for r in have_prob) / evaluated
        return {
            "evaluated": evaluated,
            "verdict_evaluated": len(have_pred),
            "accuracy_pct": round(guessed / len(have_pred) * 100.0, 1) if have_pred else None,
            "bets": staked,
            "roi_pct": round((returned - staked) / staked * 100.0, 1) if staked else None,
            "brier": round(brier, 4),
        }

    market_brier = round(sum((r["market_prob"] / 100.0 - r["is_win"]) ** 2 for r in records) / n, 4)

    return {
        "evaluated": n,
        "current": _one("current_prob", "current_pred"),
        "historical": _one("historical_prob", "historical_pred"),
        "market_brier": market_brier,
    }


def run_backtest(limit: int = 15000, since: Optional[str] = None) -> Dict[str, Any]:
    """
    Pulls up to `limit` most-recently-resolved bets (optionally only those finished at or
    after `since`, an ISO timestamp), re-scores every one with the *current* live
    ensemble's weights/calibration/threshold at its closing trajectory, and reports
    accuracy/ROI/Brier — overall, by coefficient band, and by sport — alongside the same
    metrics for (a) what was actually predicted live back when each bet happened, and
    (b) the bare bookmaker-implied probability. Saves a timestamped snapshot to disk
    (see save_and_record) so results can be compared run over run.

    Runs its entire scoring pass — market-support refresh, row fetch, and the PyTorch/
    LightGBM inference — inside pipeline._engine_lock, not just the model forward pass.
    That's deliberately the *whole* meaningful duration of the backtest, not a minimal
    critical section: a training cycle must not be able to start while a backtest is in
    progress, and a backtest that starts while training is already running must block
    and wait for it to finish rather than racing it (training and backtest used to only
    share the lock around predict_batch — the market-support refresh both call mutates
    pipeline.py's shared cache dict with no locking of its own, so calling it from
    outside the lock left a real window for the two to interleave). Aggregation and the
    on-disk save happen after the lock is released, since neither touches the live
    ensemble and there's no reason to hold training up for them.
    """
    # Imported lazily (not at module load) so this module has no import-time dependency
    # on pipeline.py's module-level side effects (constructing the live ensemble_engine
    # singleton, starting its lock) — only route handlers that actually call this ever
    # need pipeline's live state.
    from app.neuralbet.pipeline import (
        MAX_BET_COEFF, MIN_BET_EDGE_PCT, MIN_MARKET_SUPPORT, _engine_lock,
        _refresh_market_support, ensemble_engine,
    )

    t0 = time.time()
    with _engine_lock:
        market_support = _refresh_market_support()
        rows = _fetch_backtest_rows(limit=limit, since=since)
        if not rows:
            return {"status": "no_data", "samples_evaluated": 0}

        items: List[Dict[str, Any]] = []
        meta: List[Dict[str, Any]] = []
        for r in rows:
            sample = _row_to_sample(r)
            pairs = _build_full_step_pairs(sample)
            if not pairs:
                continue
            coeff = float(r["final_coefficient"] or pairs[-1][0])
            items.append({
                "step_pairs": pairs,
                "current_coeff": coeff,
                "initial_coeff": pairs[0][0],
                "factor_id": sample["factor_id"],
                "sport_path": sample["sport_path"],
                "team_1": sample["team_1"],
                "team_2": sample["team_2"],
                "overround": sample["overround_close"],
            })
            sport_top = (sample["sport_path"] or "").split("/")[0].strip() or "Другое"
            meta.append({
                "event_id": r["event_id"],
                "sport": sport_top,
                "coeff": coeff,
                "factor_id": sample["factor_id"],
                "label": sample["label"],
                "is_win": int(r["is_win"]),
                "historical_prob": r["predicted_win_probability"],
                "historical_pred": r["predicted_win"],
            })

        if not items:
            return {"status": "no_data", "samples_evaluated": 0}

        blend_weight = ensemble_engine.blend_weight
        market_weight = ensemble_engine.market_weight
        decision_threshold = ensemble_engine.decision_threshold
        # Snapshot under the same lock as the other weights above — read fresh after
        # the lock releases, this dict could be caught mid-update by a concurrent
        # tune_ensemble() call.
        sport_decision_thresholds = dict(ensemble_engine.sport_decision_thresholds)
        buckets = get_calibration_buckets()

        raw_results: List[tuple] = []
        for i in range(0, len(items), PREDICT_CHUNK):
            raw_results.extend(ensemble_engine.predict_batch(items[i:i + PREDICT_CHUNK]))

        records: List[Dict[str, Any]] = []
        for m, res in zip(meta, raw_results):
            win_prob, _error_rate, _lgb_score, _torch_score, decision_prob, _stake_logit, _exposure_logit = res
            calibrated = calibrate_probability(win_prob, buckets, sport=m["sport"], coeff=m["coeff"])
            expected_roi = ((calibrated / 100.0) * m["coeff"] - 1.0) * 100.0
            # Mirrors pipeline._place_live_bets' three gates exactly (coeff cap, minimum
            # EV, minimum market support — see those constants' docstrings in
            # pipeline.py) so current_pred answers "would today's live rules actually
            # have bet this," not just "did the decision head say win" — the whole
            # point of a backtest is previewing what live betting would produce, and the
            # two had drifted apart once the EV/support gates were added to live
            # betting but not here. Mirrors pipeline.py's own lookup — this sport's own
            # tuned decision_threshold when the snapshot above has one, the global one
            # otherwise (see NeuralBetEnsemble.sport_threshold's docstring).
            current_pred = 1 if (
                decision_prob >= sport_decision_thresholds.get(m["sport"], decision_threshold)
                and m["coeff"] <= MAX_BET_COEFF
                and expected_roi >= MIN_BET_EDGE_PCT
                and not (market_support and market_support.get((m["sport"], m["factor_id"], m["label"]), 0) < MIN_MARKET_SUPPORT)
            ) else 0
            market_prob = (min(max(1.0 / m["coeff"], 0.01), 0.99) if m["coeff"] > 1.0 else 0.99) * 100.0
            records.append({
                "event_id": m["event_id"],
                "sport": m["sport"],
                "coeff": m["coeff"],
                "coeff_bucket": coeff_bucket_index(m["coeff"]),
                "is_win": m["is_win"],
                "current_prob": calibrated,
                "current_pred": current_pred,
                "current_expected_roi": expected_roi,
                "historical_prob": m["historical_prob"],
                "historical_pred": m["historical_pred"],
                "market_prob": market_prob,
            })

    # At most one bet per event — mirrors backend/database.py's occupied_events (live
    # betting refuses a second position on a match that already has one open) and
    # model.py's _bankroll_pass (training refuses more than one position per event in a
    # round). Without this, two markets on the same match that both clear the gates
    # would each count as a separate "bet" here, overstating both the bet count and the
    # ROI/accuracy this backtest reports relative to what live betting would actually
    # place — defeating the whole point of a backtest being a preview of live rules.
    # Keeps the highest-EV candidate per event (the same ordering live candidate
    # selection sorts by) and downgrades the rest back to a plain "no bet" prediction.
    by_event: Dict[Any, List[Dict[str, Any]]] = {}
    for rec in records:
        if rec["current_pred"] == 1:
            by_event.setdefault(rec["event_id"], []).append(rec)
    for event_records in by_event.values():
        if len(event_records) < 2:
            continue
        event_records.sort(key=lambda r: r["current_expected_roi"], reverse=True)
        for rec in event_records[1:]:
            rec["current_pred"] = 0

    by_sport: Dict[str, List[Dict[str, Any]]] = {}
    by_coeff: Dict[int, List[Dict[str, Any]]] = {}
    for rec in records:
        by_sport.setdefault(rec["sport"], []).append(rec)
        by_coeff.setdefault(rec["coeff_bucket"], []).append(rec)

    result = {
        "status": "success",
        "generated_at": now_iso(),
        "duration_seconds": round(time.time() - t0, 1),
        "samples_requested": limit,
        "samples_evaluated": len(records),
        "since": since,
        "date_range": {"from": str(rows[-1]["finished_at"]), "to": str(rows[0]["finished_at"])},
        "config": {
            "blend_weight": round(blend_weight, 3),
            "market_weight": round(market_weight, 3),
            "decision_threshold": round(decision_threshold, 3),
            # Per-sport overrides actually applied to current_pred above (via
            # sport_threshold()) — only sports with their own tuned value appear here,
            # everything else used decision_threshold.
            "sport_decision_thresholds": {
                s: round(v, 3) for s, v in sport_decision_thresholds.items()
            },
            "max_bet_coeff": MAX_BET_COEFF,
        },
        "overall": _agg_group(records),
        "by_sport": sorted(
            [{"sport": s, **_agg_group(rs)} for s, rs in by_sport.items()],
            key=lambda x: -x["evaluated"],
        ),
        "by_coefficient": [
            {"bucket": COEFF_BUCKET_LABELS[b], **_agg_group(rs)}
            for b, rs in sorted(by_coeff.items())
        ],
    }

    save_and_record(result)
    return result


def save_and_record(result: Dict[str, Any]) -> None:
    """Writes the full result to a timestamped file under BACKTEST_DIR, and appends a
    condensed summary (no per-sport/per-coefficient breakdown) to history.json capped at
    MAX_HISTORY_RUNS entries — the trend list the admin panel reads without having to
    re-download every full run."""
    try:
        os.makedirs(BACKTEST_DIR, exist_ok=True)
        # Built from a fresh timestamp rather than parsing generated_at's ISO string —
        # decouples the filename from whatever UTC-offset suffix that string happens to
        # carry (previously assumed a hardcoded "+00:00" that broke once generated_at
        # switched to Moscow's "+03:00").
        ts_slug = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%dT%H-%M-%S-%f")
        with open(os.path.join(BACKTEST_DIR, f"backtest_{ts_slug}.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        history: List[Dict[str, Any]] = []
        if os.path.exists(HISTORY_PATH):
            try:
                with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = []

        history.insert(0, {
            "generated_at": result["generated_at"],
            "samples_evaluated": result["samples_evaluated"],
            "since": result.get("since"),
            "config": result["config"],
            "overall": result["overall"],
        })
        history = history[:MAX_HISTORY_RUNS]

        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error persisting backtest result: {e}")


def get_backtest_history() -> List[Dict[str, Any]]:
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading backtest history: {e}")
        return []
