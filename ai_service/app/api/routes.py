import logging
import threading

from fastapi import APIRouter, HTTPException, Body, Query
from typing import Optional, Dict, Any

logger = logging.getLogger("ai_service_routes")

_cycle_lock = threading.Lock()
_cycle_in_flight = False

from app.neuralbet import (
    run_neuralbet_inference_and_training,
    get_ai_settings,
    update_ai_settings,
    get_ai_logs,
    add_ai_log,
    reset_neural_network,
    get_reset_progress,
    get_training_health,
    run_backtest,
    get_backtest_history,
    get_latest_backtest,
    get_backtest_progress,
    BACKTEST_DEFAULT_LIMIT,
    BACKTEST_MAX_LIMIT,
    get_training_history,
)
from app.neuralbet import bankroll

router = APIRouter()

@router.get("/health")
def health_check():
    return {"status": "ok", "service": "neurobet-ai-microservice", "port": 8001}

@router.post("/predict-and-train")
def predict_and_train(payload: Dict[str, Any] = Body(default={})):
    # scrape_timestamp pins inference to the exact scrape cycle backend just committed —
    # see backend/main.py's trigger_ai_pipeline for why (bets should only ever use the
    # data from the scrape that just finished, not "whatever's freshest when this HTTP
    # request happens to be handled").
    #
    # Runs in a background thread so this single Uvicorn worker stays responsive for
    # GET /health, GET /logs, and admin polls while a cold-start or 10k training pass
    # is chewing CPU for minutes. A synchronous handler was observed to crash-loop the
    # whole container when training OOM'd or when health probes couldn't get through.
    global _cycle_in_flight
    scrape_timestamp = payload.get("scrape_timestamp")
    with _cycle_lock:
        if _cycle_in_flight:
            return {
                "status": "success",
                "result": {
                    "status": "skipped",
                    "reason": "cycle_in_flight",
                    "predictions_count": 0,
                    "finished_samples_trained": 0,
                },
            }
        _cycle_in_flight = True

    def _run() -> None:
        global _cycle_in_flight
        try:
            res = run_neuralbet_inference_and_training(scrape_timestamp=scrape_timestamp)
            logger.info("Background AI cycle finished: %s", res.get("status"))
        except Exception as e:
            add_ai_log("SYSTEM", f"AI Execution Error: {e}", level="WARNING")
            logger.error("Background AI cycle failed: %s", e, exc_info=True)
        finally:
            with _cycle_lock:
                _cycle_in_flight = False

    threading.Thread(
        target=_run,
        daemon=True,
        name="predict-and-train",
    ).start()
    return {
        "status": "success",
        "result": {
            "status": "accepted",
            "message": "cycle started in background",
            "predictions_count": 0,
            "finished_samples_trained": 0,
        },
    }

@router.get("/settings")
def read_settings():
    return {"status": "success", "settings": get_ai_settings()}

@router.post("/settings")
def write_settings(payload: Dict[str, Any] = Body(...)):
    ai_enabled = payload.get("ai_enabled")
    training_enabled = payload.get("training_enabled")
    quality_gate_bypass = payload.get("quality_gate_bypass")
    enabled_sports = payload.get("enabled_sports")
    enabled_markets = payload.get("enabled_markets")
    new_settings = update_ai_settings(
        ai_enabled=ai_enabled,
        training_enabled=training_enabled,
        quality_gate_bypass=quality_gate_bypass,
        enabled_sports=enabled_sports,
        enabled_markets=enabled_markets,
    )
    return {"status": "success", "settings": new_settings}

@router.get("/logs")
def read_logs():
    return {"status": "success", "logs": get_ai_logs()}

@router.get("/bankroll")
def read_bankroll():
    return {
        "status": "success",
        "accounts": {
            "training": bankroll.get_account("training"),
        },
        "ledger": {
            "training": bankroll.get_ledger("training", limit=100),
        },
    }

@router.post("/internal/logs")
def internal_write_logs(payload: Dict[str, Any] = Body(...)):
    """Backend calls this to narrate live-bet placement/settlement it just performed —
    backend owns live_bets/bankroll_accounts now, but ai_service still owns the log
    feed shown on the admin page, so it needs to hear about what backend did."""
    for entry in payload.get("logs", []):
        add_ai_log(
            entry.get("category", "BANKROLL"),
            entry.get("message", ""),
            level=entry.get("level", "INFO"),
        )
    return {"status": "success"}

@router.post("/bankroll/reset")
def reset_bankroll(payload: Dict[str, Any] = Body(...)):
    account = payload.get("account")
    if account not in ("training", "live"):
        raise HTTPException(status_code=400, detail="account must be 'training' or 'live'")
    start_balance = payload.get("start_balance")
    acc = bankroll.reset_account(account, start_balance=start_balance)
    add_ai_log("BANKROLL", f"{account.capitalize()} bankroll manually reset to {acc['balance']:.1f} ₽ by admin.")
    return {"status": "success", "account": acc}

@router.get("/training-health")
def training_health():
    """Traffic-light read on whether online training is helping or hurting right now —
    see get_training_health's docstring for the five-signal playbook. Also embeds the
    live quality_gate (same check that can block virtual live bets). Polled by the
    admin panel's status block. Nested under "health" (not spread into the top level)
    so its own "status" field (ok/warning/danger) can't collide with this response
    envelope's "status": "success"."""
    return {"status": "success", "health": get_training_health()}


@router.get("/hardware")
def hardware():
    """GPU snapshot for the admin panel. Backend prefers nvidia-smi on its own
    host; this is the fallback when only the AI container sees CUDA."""
    gpus: list[dict] = []
    source = None
    try:
        import subprocess
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip():
            source = "nvidia-smi"
            for line in proc.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 5:
                    continue
                try:
                    mem_used = int(float(parts[2])) * 1024 * 1024
                    mem_total = int(float(parts[3])) * 1024 * 1024
                    util = float(parts[1])
                    temp_raw = parts[4]
                    temp = None if temp_raw in ("[N/A]", "N/A", "") else float(temp_raw)
                except (TypeError, ValueError):
                    continue
                gpus.append({
                    "name": parts[0],
                    "util_percent": round(util, 1),
                    "memory": {
                        "used_bytes": mem_used,
                        "total_bytes": mem_total,
                        "free_bytes": max(mem_total - mem_used, 0),
                        "percent": round((mem_used / mem_total) * 100.0, 1) if mem_total else 0.0,
                    },
                    "temperature_c": temp,
                })
    except Exception:
        pass
    if not gpus:
        try:
            import torch
            if torch.cuda.is_available():
                source = "torch.cuda"
                for i in range(torch.cuda.device_count()):
                    free, total = torch.cuda.mem_get_info(i)
                    used = int(total) - int(free)
                    props = torch.cuda.get_device_properties(i)
                    gpus.append({
                        "name": props.name,
                        "util_percent": None,
                        "memory": {
                            "used_bytes": used,
                            "total_bytes": int(total),
                            "free_bytes": int(free),
                            "percent": round((used / total) * 100.0, 1) if total else 0.0,
                        },
                        "temperature_c": None,
                    })
        except Exception:
            pass
    if gpus:
        return {"status": "success", "gpu": {"available": True, "source": source, "gpus": gpus}}
    return {
        "status": "success",
        "gpu": {
            "available": False,
            "source": None,
            "gpus": [],
            "reason": "CUDA/nvidia-smi недоступны — обучение идёт на CPU",
        },
    }


@router.get("/reset-progress")
def reset_progress():
    """Admin poll while POST /reset-model holds the worker. Backend prefers the
    JSON file on the shared volume so this endpoint is only a fallback."""
    return {"status": "success", "progress": get_reset_progress()}

@router.post("/reset-model")
def reset_model():
    """Wipes the live model's weights/booster/blend state, training/backtest charts,
    and both bankroll accounts, then clears trained_count on the resolved-bet archive
    so training restarts from that same history — see reset_neural_network."""
    try:
        result = reset_neural_network()
        return {"status": "success", **result}
    except Exception as e:
        add_ai_log("SYSTEM", f"Neural network reset error: {e}", level="WARNING")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/backtest/progress")
def backtest_progress():
    """Admin poll while POST /backtest holds the worker. Backend prefers the JSON file
    on the shared volume so this endpoint is only a fallback."""
    return {"status": "success", "progress": get_backtest_progress()}


@router.get("/backtest/latest")
def backtest_latest(mode: str = Query("live")):
    latest = get_latest_backtest(mode=mode)
    return {"status": "success", "backtest": latest}


@router.post("/backtest")
def backtest(payload: Dict[str, Any] = Body(default={})):
    """
    Read-only evaluation of the current live ensemble against historical resolved bets
    — see app/neuralbet/backtest.py's module docstring. Runs under the same
    ensemble_engine lock as inference/training so it can't race a concurrent
    train_online() pass; on a large --limit this can take from several seconds up to
    roughly a minute, which is why the admin panel calls this through a proxy with a
    generous timeout rather than the default request timeout.

    ``mode=live`` (default) uses enabled_sports / enabled_markets and updates quality_gate / Brier.
    ``mode=full`` scores all ALLOWED_SPORTS and writes backtest_full_* only.
    """
    limit = int(payload.get("limit") or BACKTEST_DEFAULT_LIMIT)
    limit = max(100, min(limit, BACKTEST_MAX_LIMIT))
    since = payload.get("since")
    mode = payload.get("mode") or "live"
    try:
        result = run_backtest(limit=limit, since=since, mode=mode)
        return result
    except Exception as e:
        add_ai_log("SYSTEM", f"Backtest error: {e}", level="WARNING")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/backtest/review")
def backtest_review(mode: str = Query("live")):
    """Agent-oriented condensed review of the latest backtest on disk."""
    from app.neuralbet.review import build_review_from_latest

    latest = get_latest_backtest(mode=mode)
    history = get_backtest_history(mode=mode)
    review = build_review_from_latest(latest, history)
    if review and str(mode).strip().lower() == "full":
        summary = review.setdefault("summary", {})
        summary["mode"] = "full"
        summary["quality_gate_for_live"] = False
    return {
        "status": "success" if review else "no_data",
        "review": review,
        "latest_generated_at": latest.get("generated_at") if latest else None,
    }


@router.get("/backtest/history")
def backtest_history(mode: str = Query("live")):
    return {"status": "success", "runs": get_backtest_history(mode=mode)}

@router.get("/training-runs")
def training_runs():
    """Per-training-pass metrics history (val_loss/val_guess_rate/train_loss/best_epoch)
    — see app/neuralbet/training_history.py. Feeds the admin panel's training-quality
    trend chart and get_training_health's val_loss_trending_up signal; distinct from
    /training-health, which is the derived traffic-light verdict, not the raw series."""
    return {"status": "success", "runs": get_training_history()}


@router.get("/eval-snapshot")
def eval_snapshot(
    training_runs_limit: int = 40,
    logs_limit: int = 80,
    backtest_runs: int = 15,
):
    """Model-side half of the eval pack: ensemble weights, training health, recent
    training passes, condensed backtest history, and the latest *full* backtest JSON
    (with by_sport / by_coefficient). Backend adds filters, ROI, bankroll, db stats."""
    from app.neuralbet.pipeline import ensemble_engine

    e = ensemble_engine
    return {
        "status": "success",
        "settings": get_ai_settings(),
        "training_health": get_training_health(),
        "training_runs": get_training_history()[: max(0, min(training_runs_limit, 200))],
        "backtest_history": get_backtest_history()[: max(0, min(backtest_runs, 50))],
        "latest_backtest": get_latest_backtest(),
        "logs": get_ai_logs()[: max(0, min(logs_limit, 300))],
        "ensemble": {
            "blend_weight": round(e.blend_weight, 3),
            "market_weight": round(e.market_weight, 3),
            "decision_threshold": round(e.decision_threshold, 3),
            "sport_decision_thresholds": {
                k: round(v, 3) for k, v in e.sport_decision_thresholds.items()
            },
        },
    }

