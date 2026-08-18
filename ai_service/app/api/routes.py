from fastapi import APIRouter, HTTPException, Body
from typing import Optional, Dict, Any

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
    get_training_history,
)
from app.neuralbet import bankroll
from app.deepseek import test_deepseek_web

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
    scrape_timestamp = payload.get("scrape_timestamp")
    try:
        res = run_neuralbet_inference_and_training(scrape_timestamp=scrape_timestamp)
        return {"status": "success", "result": res}
    except Exception as e:
        add_ai_log("SYSTEM", f"AI Execution Error: {e}", level="WARNING")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/settings")
def read_settings():
    return {"status": "success", "settings": get_ai_settings()}

@router.post("/settings")
def write_settings(payload: Dict[str, Any] = Body(...)):
    ai_enabled = payload.get("ai_enabled")
    training_enabled = payload.get("training_enabled")
    new_settings = update_ai_settings(ai_enabled=ai_enabled, training_enabled=training_enabled)
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
    see get_training_health's docstring for the three-signal playbook. Polled by the
    admin panel's status block. Nested under "health" (not spread into the top level)
    so its own "status" field (ok/warning/danger) can't collide with this response
    envelope's "status": "success"."""
    return {"status": "success", "health": get_training_health()}

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

@router.post("/backtest")
def backtest(payload: Dict[str, Any] = Body(default={})):
    """
    Read-only evaluation of the current live ensemble against historical resolved bets
    — see app/neuralbet/backtest.py's module docstring. Runs under the same
    ensemble_engine lock as inference/training so it can't race a concurrent
    train_online() pass; on a large --limit this can take from several seconds up to
    roughly a minute, which is why the admin panel calls this through a proxy with a
    generous timeout rather than the default request timeout.
    """
    limit = int(payload.get("limit") or 15000)
    limit = max(100, min(limit, 50000))
    since = payload.get("since")
    try:
        result = run_backtest(limit=limit, since=since)
        return result
    except Exception as e:
        add_ai_log("SYSTEM", f"Backtest error: {e}", level="WARNING")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/backtest/history")
def backtest_history():
    return {"status": "success", "runs": get_backtest_history()}

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

@router.post("/deepseek/ask")
def ask_deepseek(payload: Dict[str, Any] = Body(...)):
    prompt = payload.get("prompt", "Привет! Подтверди готовность.")
    res = test_deepseek_web(prompt)
    if res.get("status") == "error":
        raise HTTPException(status_code=500, detail=res.get("error"))
    return res
