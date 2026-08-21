"""
Offline evaluation of the *current, already-trained* ensemble against historical
resolved bets — answers "if today's weights/calibration/threshold had been live back
then, would the numbers actually look better?" without waiting days for the online
trainer to accumulate enough new matches to tell.

Deliberately NOT the same thing as the online trainer's val_loss/val_guess_rate: those
measure the model against a fixed ~400-sample validation slice on a random trajectory
cutoff, refreshed with every online-training pass. This runs a much larger, deterministic
sweep (the last in-band snapshot — the moment live staking could actually have
happened, not the 1.01 close), broken down by coefficient band and sport the same way the
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
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import torch

from app.config import MODEL_DIR
from app.core.database import get_finished_connection, release_connection
from app.neuralbet import bankroll
from app.neuralbet.bankroll import allocate
from app.neuralbet.calibration import calibrate_probability, coeff_bucket_index, get_calibration_buckets
from neurobet_filters import (
    universe_sql,
    universe_sql_params,
    passes_live_gates,
    in_bet_band,
    in_live_stake_sport,
    in_live_stake_market,
    MIN_BET_COEFF,
    MAX_BET_COEFF,
)
from neurobet_features import (
    MARKET_FAMILIES,
    build_model_input,
    build_team_form_asof_lookup,
    market_family_index,
    no_vig_probability,
    row_to_sample,
    set_team_form_cache,
)
from neurobet_features.view import LGB_DISABLE_TEAM_FEATURES

from app.neuralbet.quality_gate import (  # noqa: F401 — re-export for callers/tests
    LIVE_QUALITY_GATE,
    LIVE_QUALITY_MIN_BETS,
    QUALITY_GATE_MAX_AGE_HOURS,
    QUALITY_GATE_MIN_CONSECUTIVE,
    QUALITY_GATE_MIN_SAMPLES,
    QUALITY_GATE_SAMPLE_TOLERANCE,
    evaluate_quality_gate,
)

logger = logging.getLogger("ai_service_backtest")

BACKTEST_DIR = os.path.join(MODEL_DIR, "backtests")
HISTORY_PATH = os.path.join(BACKTEST_DIR, "history.json")
# 50 -> 180: backtest fires automatically every hour on the hour (Moscow — see
# ai_service/main.py) plus manual admin-panel runs. 180 ≈ 7.5 days of hourly auto-runs
# plus headroom for manual ones, cheap either way (this is just JSON).
MAX_HISTORY_RUNS = 180

COEFF_BUCKET_LABELS = ["1.0–1.5", "1.5–2.0", "2.0–3.0", "3.0–5.0", "5.0–10.0", "10.0+"]

# predict_batch's own forward pass is cheap per-sample, but chunking bounds peak memory
# for a large --limit run instead of building one giant tensor up front.
PREDICT_CHUNK = 4000

BACKTEST_DEFAULT_LIMIT = int(os.getenv("NEURALBET_BACKTEST_DEFAULT_LIMIT", "80000"))
BACKTEST_MAX_LIMIT = int(os.getenv("NEURALBET_BACKTEST_MAX_LIMIT", "100000"))
OOS_TEST_EVENT_FRACTION = float(os.getenv("NEURALBET_OOS_TEST_EVENT_FRACTION", "0.15"))
OOS_MIN_EVENTS = int(os.getenv("NEURALBET_OOS_MIN_EVENTS", "40"))
BOOTSTRAP_SAMPLES = int(os.getenv("NEURALBET_BOOTSTRAP_SAMPLES", "500"))
BOOTSTRAP_SEED = int(os.getenv("NEURALBET_BOOTSTRAP_SEED", "42"))
WALK_FORWARD_FOLDS = int(os.getenv("NEURALBET_WALK_FORWARD_FOLDS", "4"))
WALK_FORWARD_REGION_FRACTION = float(os.getenv("NEURALBET_WALK_FORWARD_REGION_FRACTION", "0.40"))

BACKTEST_PROGRESS_PATH = os.path.join(MODEL_DIR, "backtest_progress.json")
_backtest_progress_lock = threading.Lock()
_backtest_progress: dict[str, Any] = {
    "active": False,
    "step": "idle",
    "label": "",
    "pct": 0,
    "processed": 0,
    "total": 0,
}


def _persist_backtest_progress(payload: dict[str, Any]) -> None:
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)
        tmp_path = BACKTEST_PROGRESS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, BACKTEST_PROGRESS_PATH)
    except Exception as e:
        logger.error(f"Error persisting backtest progress: {e}")


def set_backtest_progress(
    step: str,
    label: str,
    pct: int,
    *,
    active: bool = True,
    processed: int = 0,
    total: int = 0,
) -> None:
    with _backtest_progress_lock:
        _backtest_progress.update({
            "active": active,
            "step": step,
            "label": label,
            "pct": max(0, min(int(pct), 100)),
            "processed": max(0, int(processed)),
            "total": max(0, int(total)),
        })
        payload = dict(_backtest_progress)
    _persist_backtest_progress(payload)


def get_backtest_progress() -> dict[str, Any]:
    with _backtest_progress_lock:
        return dict(_backtest_progress)

# Same fixed +3 offset pipeline.py's now_moscow() uses — "generated_at" (and the
# filename slug derived from it, see save_and_record) is what the admin panel's
# "История запусков" table and the on-disk backtest_*.json filenames show, and both
# were showing UTC, off by 3 hours from every other timestamp in the app.
MOSCOW_TZ = timezone(timedelta(hours=3))


def now_iso() -> str:
    return datetime.now(MOSCOW_TZ).isoformat()


def _fetch_backtest_rows(limit: int, since: Optional[str]) -> List[Any]:
    from app.neuralbet.pipeline import _track_conn, _untrack_conn
    f_conn = _track_conn(get_finished_connection())
    try:
        f_cursor = f_conn.cursor()
        where_since = "AND h.finished_at >= %s" if since else ""
        params: List[Any] = [since] if since else []
        sports, factors = universe_sql_params()
        f_cursor.execute(f"""
            SELECT h.event_id, h.factor_id, h.label, h.parameter, h.market_prefix, h.is_win,
                   h.odds_seq_json, h.score_seq_json, h.score_sum_seq_json,
                   h.ts_seq_json, h.timer_seq_json, h.overround_seq_json,
                   h.score_diff_at_bet, h.finished_at, h.overround_close, h.trained_count,
                   h.final_coefficient, h.predicted_win_probability, h.predicted_win,
                   f.sport_path, f.team_1, f.team_2
            FROM finished_bets h
            JOIN finished_events f ON h.event_id = f.event_id
            WHERE h.is_win IS NOT NULL {where_since}
              {universe_sql("f", "h")}
            ORDER BY h.finished_at DESC
            LIMIT %s
        """, params + [sports, factors, limit])
        rows = f_cursor.fetchall()
        return rows
    finally:
        _untrack_conn(f_conn)
        release_connection(f_conn)


def _brier(prob_pct: float, is_win: int) -> float:
    p = prob_pct / 100.0
    return (p - is_win) ** 2


def _probability_metrics(records: List[Dict[str, Any]], prob_key: str) -> Optional[Dict[str, Any]]:
    have = [r for r in records if r.get(prob_key) is not None]
    if not have:
        return None
    n = len(have)
    brier = sum(_brier(r[prob_key], r["is_win"]) for r in have) / n
    return {"evaluated": n, "brier": round(brier, 4)}


def _guess_accuracy_pct(records: List[Dict[str, Any]], pred_key: str) -> Optional[float]:
    """
    Share of rows where the binary verdict matched the outcome (guessed / угадано).

    Same definition as the pre-refactor backtest and finished_bets history:
    predicted_win == is_win, including correct «не ставить / проиграет» calls.
    """
    have = [r for r in records if r.get(pred_key) is not None]
    if not have:
        return None
    guessed = sum(1 for r in have if int(r[pred_key]) == int(r["is_win"]))
    return round(guessed / len(have) * 100.0, 1)


def _verdict_metrics(records: List[Dict[str, Any]], pred_key: str) -> Optional[Dict[str, Any]]:
    have = [r for r in records if r.get(pred_key) is not None]
    if not have:
        return None
    pos = [r for r in have if int(r[pred_key]) == 1]
    guessed = sum(1 for r in pos if r["is_win"] == 1)
    return {
        "evaluated": len(have),
        "verdict_positive": len(pos),
        "precision_pct": round(guessed / len(pos) * 100.0, 1) if pos else None,
        "recall_pct": round(
            guessed / sum(1 for r in have if r["is_win"] == 1) * 100.0, 1
        ) if any(r["is_win"] == 1 for r in have) else None,
    }


def _stake_metrics(records: List[Dict[str, Any]], pred_key: str) -> Optional[Dict[str, Any]]:
    bets = [r for r in records if r.get(pred_key) is not None and int(r[pred_key]) == 1]
    if not bets:
        return {"bets": 0, "roi_pct": None, "win_rate_pct": None, "break_even_pct": None}
    staked = len(bets)
    wins = sum(1 for r in bets if r["is_win"] == 1)
    returned = sum(r["coeff"] for r in bets if r["is_win"] == 1)
    avg_coeff = sum(r["coeff"] for r in bets) / staked
    return {
        "bets": staked,
        "win_rate_pct": round(wins / staked * 100.0, 1),
        "break_even_pct": round(100.0 / avg_coeff, 1) if avg_coeff > 0 else None,
        "roi_pct": round((returned - staked) / staked * 100.0, 1),
    }


def _bootstrap_roi_ci(records: List[Dict[str, Any]], pred_key: str) -> Optional[Dict[str, float]]:
    import random as _random

    by_event: Dict[Any, List[Dict[str, Any]]] = {}
    for r in records:
        if r.get(pred_key) == 1:
            by_event.setdefault(r["event_id"], []).append(r)
    events = list(by_event.keys())
    if len(events) < 5:
        return None
    rng = _random.Random(BOOTSTRAP_SEED)
    rois: List[float] = []
    for _ in range(BOOTSTRAP_SAMPLES):
        sample_events = [rng.choice(events) for _ in range(len(events))]
        staked = 0
        returned = 0.0
        for eid in sample_events:
            for r in by_event[eid]:
                staked += 1
                if r["is_win"] == 1:
                    returned += r["coeff"]
        if staked:
            rois.append((returned - staked) / staked * 100.0)
    if not rois:
        return None
    rois.sort()
    lo = rois[int(0.025 * len(rois))]
    hi = rois[int(0.975 * len(rois))]
    return {"roi_pct_lo": round(lo, 1), "roi_pct_hi": round(hi, 1)}


def _bankroll_replay_metrics(
    records: List[Dict[str, Any]],
    pred_key: str = "current_pred",
    start_balance: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Kelly replay with stake head — mirrors live allocate() sizing."""
    bets = [r for r in records if r.get(pred_key) == 1 and r.get("stake_logit") is not None]
    if not bets:
        return {"bets": 0, "bankroll_roi_pct": None, "bank_end": None}
    start = float(start_balance if start_balance is not None else bankroll.START_BALANCE)
    bank = start
    bets.sort(key=lambda r: (str(r.get("finished_at") or ""), r["event_id"]))

    # Group bets that share the same finish timestamp into one allocation round.
    rounds: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    last_ts = None
    for bet in bets:
        ts = str(bet.get("finished_at") or "")
        if current and ts != last_ts:
            rounds.append(current)
            current = []
        current.append(bet)
        last_ts = ts
    if current:
        rounds.append(current)

    placed = 0
    for round_bets in rounds:
        win_probs = torch.tensor(
            [b["current_prob"] / 100.0 for b in round_bets], dtype=torch.float32
        )
        coeffs = torch.tensor([b["coeff"] for b in round_bets], dtype=torch.float32)
        stake_logits = torch.tensor(
            [float(b["stake_logit"]) for b in round_bets], dtype=torch.float32
        )
        fractions = allocate(win_probs, coeffs, stake_logits)
        for frac, bet in zip(fractions.tolist(), round_bets):
            stake = bank * frac
            if stake < bank * bankroll.MIN_STAKE_FRACTION:
                continue
            placed += 1
            if bet["is_win"] == 1:
                bank += stake * (bet["coeff"] - 1.0)
            else:
                bank -= stake

    if placed == 0:
        return {"bets": 0, "bankroll_roi_pct": None, "bank_end": round(bank, 2)}
    return {
        "bets": placed,
        "bankroll_roi_pct": round((bank - start) / start * 100.0, 1),
        "bank_end": round(bank, 2),
    }


def _dedupe_one_bet_per_event(records: List[Dict[str, Any]], pred_key: str = "current_pred") -> None:
    by_event: Dict[Any, List[Dict[str, Any]]] = {}
    for rec in records:
        if rec.get(pred_key) == 1:
            by_event.setdefault(rec["event_id"], []).append(rec)
    for event_records in by_event.values():
        if len(event_records) < 2:
            continue
        event_records.sort(key=lambda r: r.get("current_expected_roi", 0), reverse=True)
        for rec in event_records[1:]:
            rec[pred_key] = 0


def _records_from_scored(
    scored: List[Dict[str, Any]],
    buckets: Dict[Any, Tuple[float, float]],
    decision_threshold: float,
    sport_decision_thresholds: Dict[str, float],
    market_support: Optional[dict],
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for m in scored:
        win_prob = m["raw_win_prob"]
        decision_prob = m["decision_prob"]
        calibrated = calibrate_probability(
            win_prob, buckets, sport=m["sport"], coeff=m["coeff"],
        )
        expected_roi = ((calibrated / 100.0) * m["coeff"] - 1.0) * 100.0
        thr = sport_decision_thresholds.get(m["sport"], decision_threshold)
        current_verdict = 1 if decision_prob >= thr else 0
        support_count = None
        if market_support:
            support_count = market_support.get((m["sport"], m["factor_id"], m["label"]), 0)
        current_pred = 1 if (
            current_verdict == 1
            and passes_live_gates(
                m["coeff"],
                expected_roi,
                support_count,
                sport_path=m["sport_path"],
                factor_id=m["factor_id"],
            )
        ) else 0
        market_prob = (
            (min(max(1.0 / m["coeff"], 0.01), 0.99) if m["coeff"] > 1.0 else 0.99) * 100.0
        )
        nv = no_vig_probability(m["coeff"], m.get("overround_close")) * 100.0
        market_family = market_family_index(m["factor_id"])
        records.append({
            "event_id": m["event_id"],
            "sport": m["sport"],
            "sport_path": m.get("sport_path"),
            "coeff": m["coeff"],
            "coeff_bucket": coeff_bucket_index(m["coeff"]),
            "market_family": market_family,
            "market_label": MARKET_FAMILIES[market_family] if market_family < len(MARKET_FAMILIES) else "other",
            "is_win": m["is_win"],
            "trained_count": m["trained_count"],
            "finished_at": m.get("finished_at"),
            "current_prob": calibrated,
            "current_verdict": current_verdict,
            "stake_candidate": current_pred,
            "current_pred": current_pred,
            "current_expected_roi": expected_roi,
            "stake_logit": m.get("stake_logit"),
            "raw_win_prob": win_prob,
            "decision_prob": decision_prob,
            "historical_prob": m["historical_prob"],
            "historical_pred": m["historical_pred"],
            "market_prob": market_prob,
            "no_vig_prob": nv,
        })
    _dedupe_one_bet_per_event(records)
    return records


def _walk_forward_folds(event_order: List[Any]) -> List[Tuple[int, set]]:
    n = len(event_order)
    if WALK_FORWARD_FOLDS < 2 or n < OOS_MIN_EVENTS:
        return []
    region_start = max(0, int(n * (1.0 - WALK_FORWARD_REGION_FRACTION)))
    region = event_order[region_start:]
    if len(region) < WALK_FORWARD_FOLDS * 5:
        return []
    chunk = max(len(region) // WALK_FORWARD_FOLDS, 5)
    folds: List[Tuple[int, set]] = []
    for i in range(WALK_FORWARD_FOLDS):
        start = i * chunk
        end = start + chunk if i < WALK_FORWARD_FOLDS - 1 else len(region)
        if end - start < 5:
            continue
        folds.append((i + 1, set(region[start:end])))
    return folds


def _event_ts_map(rows: List[Any]) -> Dict[Any, Any]:
    event_ts: Dict[Any, Any] = {}
    for r in rows:
        eid = r["event_id"]
        ts = r["finished_at"]
        if eid not in event_ts or str(ts) > str(event_ts[eid]):
            event_ts[eid] = ts
    return event_ts


def _walk_forward_eval(
    scored: List[Dict[str, Any]],
    rows: List[Any],
    decision_threshold: float,
    sport_decision_thresholds: Dict[str, float],
    market_support: Optional[dict],
) -> Optional[Dict[str, Any]]:
    event_ts = _event_ts_map(rows)
    event_order = sorted(event_ts.keys(), key=lambda e: str(event_ts[e]))
    folds = _walk_forward_folds(event_order)
    if not folds:
        return None

    fold_rows: List[Dict[str, Any]] = []
    agg_records: List[Dict[str, Any]] = []
    for fold_idx, fold_events in folds:
        fold_ts = min(str(event_ts[e]) for e in fold_events)
        buckets = get_calibration_buckets(before=fold_ts)
        fold_scored = [s for s in scored if s["event_id"] in fold_events]
        fold_records = _records_from_scored(
            fold_scored, buckets, decision_threshold, sport_decision_thresholds, market_support,
        )
        agg_records.extend(fold_records)
        fold_agg = _agg_group(fold_records)
        if fold_agg:
            fold_rows.append({"fold": fold_idx, "events": len(fold_events), **fold_agg})

    if not agg_records:
        return None
    combined = _agg_group(agg_records)
    return {
        "folds": fold_rows,
        "combined": combined,
        "meta": {
            "kind": "temporal_calibration_slice",
            "retrain_per_fold": False,
            "note": (
                "Per-fold calibration cutoff on a late temporal region; "
                "same frozen ensemble weights — not rolling retrain OOS."
            ),
        },
    } if combined else None


def _policy_would_bet(record: Dict[str, Any], policy: str) -> bool:
    coeff = float(record.get("coeff") or 0)
    expected_roi = float(record.get("current_expected_roi") or 0)
    verdict = int(record.get("current_verdict") or 0)
    sport_path = record.get("sport_path")
    market_label = record.get("market_label")
    if policy == "decision_and_ev":
        return int(record.get("current_pred") or 0) == 1
    if policy == "ev_only":
        return passes_live_gates(
            coeff, expected_roi, sport_path=sport_path, market_label=market_label,
        )
    if policy == "decision_only":
        if verdict != 1:
            return False
        if not in_bet_band(coeff):
            return False
        if sport_path is not None and not in_live_stake_sport(sport_path):
            return False
        if market_label is not None and not in_live_stake_market(market_label=market_label):
            return False
        return True
    return False


def _apply_policy_preds(records: List[Dict[str, Any]], policy: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rec in records:
        clone = dict(rec)
        clone["_policy_pred"] = 1 if _policy_would_bet(rec, policy) else 0
        out.append(clone)
    _dedupe_one_bet_per_event(out, pred_key="_policy_pred")
    return out


def _policy_ablation(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    policies = ("decision_and_ev", "ev_only", "decision_only")
    out: Dict[str, Any] = {}
    for policy in policies:
        scoped = _apply_policy_preds(records, policy)
        stake = _stake_metrics(scoped, "_policy_pred")
        ci = _bootstrap_roi_ci(scoped, "_policy_pred")
        out[policy] = {**(stake or {}), **(ci or {})}
    return out


def _agg_group(records: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    n = len(records)
    if n == 0:
        return None

    market_brier = round(sum(_brier(r["market_prob"], r["is_win"]) for r in records) / n, 4)
    no_vig_brier = round(sum(_brier(r["no_vig_prob"], r["is_win"]) for r in records) / n, 4)

    current_stake = _stake_metrics(records, "current_pred")
    stake_ci = _bootstrap_roi_ci(records, "current_pred")
    bankroll_stake = _bankroll_replay_metrics(records, "current_pred")

    flat_bets = (current_stake or {}).get("bets", 0)
    current_stake_out: Dict[str, Any] = {
        **(current_stake or {}),
        **(stake_ci or {}),
        "flat_bets": flat_bets,
        "bets": flat_bets,
    }
    if bankroll_stake:
        current_stake_out["kelly_bets"] = bankroll_stake.get("bets")
        current_stake_out["bankroll_roi_pct"] = bankroll_stake.get("bankroll_roi_pct")
        current_stake_out["bank_end"] = bankroll_stake.get("bank_end")
        current_stake_out["kelly"] = bankroll_stake

    return {
        "evaluated": n,
        "probability": {
            "current": _probability_metrics(records, "current_prob"),
            "historical": _probability_metrics(records, "historical_prob"),
            "market_raw": {"evaluated": n, "brier": market_brier},
            "market_no_vig": {"evaluated": n, "brier": no_vig_brier},
        },
        "verdict": {
            "current": _verdict_metrics(records, "current_verdict"),
            "historical": _verdict_metrics(records, "historical_pred"),
        },
        "stake_policy": {
            "current": current_stake_out,
            "historical": _stake_metrics(records, "historical_pred"),
        },
        # Legacy flat fields for history.json / admin trend charts.
        "market_brier": market_brier,
        "current": {
            "evaluated": n,
            "accuracy_pct": _guess_accuracy_pct(records, "current_pred"),
            "verdict_accuracy_pct": _guess_accuracy_pct(records, "current_verdict"),
            "bets": (current_stake or {}).get("bets", 0),
            "roi_pct": (current_stake or {}).get("roi_pct"),
            "brier": (_probability_metrics(records, "current_prob") or {}).get("brier"),
        },
        "historical": {
            "evaluated": n,
            "accuracy_pct": _guess_accuracy_pct(records, "historical_pred"),
            "bets": (_stake_metrics(records, "historical_pred") or {}).get("bets", 0),
            "roi_pct": (_stake_metrics(records, "historical_pred") or {}).get("roi_pct"),
            "brier": (_probability_metrics(records, "historical_prob") or {}).get("brier"),
        },
    }


def run_backtest(limit: int = BACKTEST_DEFAULT_LIMIT, since: Optional[str] = None) -> Dict[str, Any]:
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
        _engine_lock,
        _refresh_market_support, ensemble_engine,
        cycle_aborted,
    )

    t0 = time.time()
    set_backtest_progress(
        "starting",
        f"Запуск бэктеста на {limit:,} ставок…".replace(",", " "),
        1,
        total=limit,
    )
    try:
        set_backtest_progress("waiting_lock", "Жду завершения обучения или другого бэктеста…", 3, total=limit)
        with _engine_lock:
            if cycle_aborted():
                set_backtest_progress("error", "Прервано", 0, active=False)
                return {"status": "aborted", "samples_evaluated": 0}
            set_backtest_progress("fetch", "Загружаю архив завершённых ставок…", 8, total=limit)
            market_support = _refresh_market_support()
            rows = _fetch_backtest_rows(limit=limit, since=since)
            if not rows:
                set_backtest_progress("error", "Нет данных для бэктеста", 0, active=False)
                return {"status": "no_data", "samples_evaluated": 0}

            items: List[Dict[str, Any]] = []
            meta: List[Dict[str, Any]] = []
            row_total = len(rows)
            form_lookup = build_team_form_asof_lookup([
                {
                    "event_id": r["event_id"],
                    "factor_id": r["factor_id"],
                    "parameter": r.get("parameter") or "",
                    "market_prefix": r.get("market_prefix") or "",
                    "team_1": r.get("team_1"),
                    "team_2": r.get("team_2"),
                    "sport_path": r.get("sport_path"),
                    "is_win": r["is_win"],
                    "finished_at": r["finished_at"],
                }
                for r in rows
            ])
            calibration_cutoff = str(rows[-1]["finished_at"])
            for idx, r in enumerate(rows):
                sample = row_to_sample(r)
                bet_key = (
                    r["event_id"],
                    int(sample["factor_id"] or 0),
                    sample.get("parameter") or "",
                    sample.get("market_prefix") or "",
                )
                t1_form, t2_form = form_lookup.get(bet_key, (None, None))
                if t1_form is not None:
                    sample["team1_form_asof"] = t1_form
                if t2_form is not None:
                    sample["team2_form_asof"] = t2_form
                view = build_model_input(sample, mode="backtest")
                if view is None:
                    continue
                coeff = float(view["current_coeff"])
                items.append(view)
                sport_name = (sample["sport_path"] or "").split("/")[0].strip() or "Другое"
                meta.append({
                    "event_id": r["event_id"],
                    "sport": sport_name,
                    "sport_path": sample["sport_path"],
                    "coeff": coeff,
                    "factor_id": sample["factor_id"],
                    "label": sample["label"],
                    "is_win": int(r["is_win"]),
                    "trained_count": int(r.get("trained_count") or 0),
                    "overround_close": r.get("overround_close"),
                    "finished_at": r.get("finished_at"),
                    "historical_prob": r["predicted_win_probability"],
                    "historical_pred": r["predicted_win"],
                })
                if idx > 0 and idx % 5000 == 0:
                    pct = 8 + int(7 * idx / row_total)
                    set_backtest_progress(
                        "prepare",
                        f"Подготовка {idx:,}/{row_total:,}…".replace(",", " "),
                        pct,
                        processed=idx,
                        total=row_total,
                    )

            if not items:
                set_backtest_progress("error", "Нет валидных срезов для бэктеста", 0, active=False)
                return {"status": "no_data", "samples_evaluated": 0}

            set_backtest_progress(
                "prepare",
                f"Готово {len(items):,} срезов — запуск инференса…".replace(",", " "),
                15,
                processed=len(items),
                total=len(items),
            )

            blend_weight = ensemble_engine.blend_weight
            market_weight = ensemble_engine.market_weight
            decision_threshold = ensemble_engine.decision_threshold
            # Snapshot under the same lock as the other weights above — read fresh after
            # the lock releases, this dict could be caught mid-update by a concurrent
            # tune_ensemble() call. Apply sport floors first so a football (etc.) minimum
            # is in the copy even if this process loaded a checkpoint from before the floor.
            ensemble_engine._apply_sport_threshold_floors()
            sport_decision_thresholds = dict(ensemble_engine.sport_decision_thresholds)
            buckets = get_calibration_buckets(before=calibration_cutoff)

            raw_results: List[tuple] = []
            n_items = len(items)
            for i in range(0, n_items, PREDICT_CHUNK):
                if cycle_aborted():
                    set_backtest_progress("error", "Прервано", 0, active=False)
                    return {"status": "aborted", "samples_evaluated": 0}
                chunk_end = min(i + PREDICT_CHUNK, n_items)
                raw_results.extend(ensemble_engine.predict_batch(items[i:chunk_end]))
                pct = 15 + int(70 * chunk_end / n_items)
                set_backtest_progress(
                    "predict",
                    f"Инференс {chunk_end:,}/{n_items:,}…".replace(",", " "),
                    pct,
                    processed=chunk_end,
                    total=n_items,
                )

            scored: List[Dict[str, Any]] = []
            for m, res in zip(meta, raw_results):
                win_prob, _error_rate, _lgb_score, _torch_score, decision_prob, stake_logit, _exposure_logit = res
                scored.append({
                    **m,
                    "raw_win_prob": win_prob,
                    "decision_prob": decision_prob,
                    "stake_logit": stake_logit,
                })

            records = _records_from_scored(
                scored, buckets, decision_threshold, sport_decision_thresholds, market_support,
            )

        set_backtest_progress("aggregate", "Агрегация и фильтр по матчам…", 88, processed=len(records), total=len(records))

        by_sport: Dict[str, List[Dict[str, Any]]] = {}
        by_coeff: Dict[int, List[Dict[str, Any]]] = {}
        by_market: Dict[str, List[Dict[str, Any]]] = {}
        for rec in records:
            by_sport.setdefault(rec["sport"], []).append(rec)
            by_coeff.setdefault(rec["coeff_bucket"], []).append(rec)
            by_market.setdefault(rec["market_label"], []).append(rec)

        event_ts = _event_ts_map(rows)
        event_order = sorted(event_ts.keys(), key=lambda e: str(event_ts[e]))
        n_hold = max(OOS_MIN_EVENTS, int(len(event_order) * OOS_TEST_EVENT_FRACTION))
        hold_events = set(event_order[-n_hold:]) if len(event_order) >= OOS_MIN_EVENTS else set()
        oos_records = [
            r for r in records
            if r["event_id"] in hold_events and int(r.get("trained_count") or 0) == 0
        ]
        oos_by_market_groups: Dict[str, List[Dict[str, Any]]] = {}
        for rec in oos_records:
            oos_by_market_groups.setdefault(rec["market_label"], []).append(rec)

        # Targeted OOS ablations (diagnostics before any model reset): sport×market
        # pockets that look strong in-sample must clear the same never-train holdout.
        oos_total_over = [r for r in oos_records if r["market_label"] == "total_over"]
        oos_tt_total_over = [
            r for r in oos_records
            if r["market_label"] == "total_over"
            and str(r.get("sport") or "").strip().lower() == "настольный теннис"
        ]
        oos_ablation = {
            "table_tennis_x_total_over": (
                _agg_group(oos_tt_total_over) if oos_tt_total_over else None
            ),
            "total_over": _agg_group(oos_total_over) if oos_total_over else None,
        }

        oos_ids = {id(r) for r in oos_records}
        in_sample_records = [r for r in records if id(r) not in oos_ids]

        walk_forward = _walk_forward_eval(
            scored, rows, decision_threshold, sport_decision_thresholds, market_support,
        )
        walk_forward_combined = (walk_forward or {}).get("combined") if walk_forward else None
        walk_forward_meta = (walk_forward or {}).get("meta") if walk_forward else None

        set_backtest_progress("save", "Сохранение результата…", 95, processed=len(records), total=len(records))

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
                "sport_decision_thresholds": {
                    s: round(v, 3) for s, v in sport_decision_thresholds.items()
                },
                "max_bet_coeff": MAX_BET_COEFF,
                "min_bet_coeff": MIN_BET_COEFF,
                "oos_holdout_events": len(hold_events),
                "calibration_cutoff": calibration_cutoff,
                "walk_forward_folds": WALK_FORWARD_FOLDS,
                "lgb_disable_team_features": LGB_DISABLE_TEAM_FEATURES,
                "bootstrap_seed": BOOTSTRAP_SEED,
            },
            "overall": _agg_group(records),
            "in_sample": _agg_group(in_sample_records),
            "oos_never_train": _agg_group(oos_records) if oos_records else None,
            "walk_forward": walk_forward_combined,
            "walk_forward_folds": (walk_forward or {}).get("folds") if walk_forward else None,
            "walk_forward_meta": walk_forward_meta,
            "policy_ablation_oos": _policy_ablation(oos_records) if oos_records else None,
            "oos_by_market": sorted(
                [{"market": m, **_agg_group(rs)} for m, rs in oos_by_market_groups.items()],
                key=lambda x: -x["evaluated"],
            ) if oos_by_market_groups else None,
            "oos_ablation": oos_ablation,
            "by_sport": sorted(
                [{"sport": s, **_agg_group(rs)} for s, rs in by_sport.items()],
                key=lambda x: -x["evaluated"],
            ),
            "by_market": sorted(
                [{"market": m, **_agg_group(rs)} for m, rs in by_market.items()],
                key=lambda x: -x["evaluated"],
            ),
            "by_coefficient": [
                {"bucket": COEFF_BUCKET_LABELS[b], **_agg_group(rs)}
                for b, rs in sorted(by_coeff.items())
            ],
        }
        prior_history = get_backtest_history()
        result["quality_gate"] = evaluate_quality_gate(result, history=prior_history)
        from app.neuralbet.review import build_agent_review

        result["agent_review"] = build_agent_review(result, records=records, history=prior_history)
        try:
            from app.deepseek.insights import build_backtest_narrative, llm_is_enabled

            if llm_is_enabled():
                narrative = build_backtest_narrative(result["agent_review"])
                if narrative:
                    result["agent_review"]["llm_narrative"] = narrative
        except Exception as e:
            logger.warning("Backtest LLM narrative skipped: %s", e)

        save_and_record(result)
        set_backtest_progress(
            "done",
            f"Готово — {len(records):,} ставок за {round(time.time() - t0, 1)}с".replace(",", " "),
            100,
            active=False,
            processed=len(records),
            total=len(records),
        )
        return result
    except Exception as e:
        set_backtest_progress("error", f"Ошибка: {e}", 0, active=False)
        raise


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
            "samples_requested": result.get("samples_requested"),
            "since": result.get("since"),
            "config": result["config"],
            "overall": result["overall"],
            "quality_gate": result.get("quality_gate"),
            "agent_review": result.get("agent_review"),
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


def get_latest_backtest() -> Optional[Dict[str, Any]]:
    """Full last run (by_sport / by_coefficient), not the condensed history.json row.
    Used by the eval-pack so an agent can judge the model without a separate download."""
    if not os.path.isdir(BACKTEST_DIR):
        return None
    files = [
        os.path.join(BACKTEST_DIR, name)
        for name in os.listdir(BACKTEST_DIR)
        if name.startswith("backtest_") and name.endswith(".json")
    ]
    if not files:
        return None
    latest = max(files, key=os.path.getmtime)
    try:
        with open(latest, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading latest backtest {latest}: {e}")
        return None


def clear_backtest_history() -> None:
    """Removes history.json and every full backtest_*.json so QualityTrendChart and
    get_latest_backtest start empty after a model reset."""
    try:
        if os.path.exists(HISTORY_PATH):
            os.remove(HISTORY_PATH)
        if os.path.isdir(BACKTEST_DIR):
            for name in os.listdir(BACKTEST_DIR):
                if name.startswith("backtest_") and name.endswith(".json"):
                    try:
                        os.remove(os.path.join(BACKTEST_DIR, name))
                    except Exception:
                        pass
    except Exception as e:
        logger.error(f"Error clearing backtest history: {e}")
