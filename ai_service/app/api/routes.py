from fastapi import APIRouter, HTTPException, Body
from typing import Optional, Dict, Any

from app.neuralbet import (
    run_neuralbet_inference_and_training,
    get_ai_settings,
    update_ai_settings,
    get_ai_logs,
    add_ai_log,
    reset_neural_network,
    get_training_health,
    run_backtest,
    get_backtest_history,
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

@router.post("/reset-model")
def reset_model():
    """Wipes the live model's weights/booster/blend state back to a fresh, untrained
    ensemble and clears trained_count on the resolved-bet archive so training restarts
    from scratch using that same existing history — see reset_neural_network's
    docstring for why this is deliberately not the same thing as reset-db/all."""
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

@router.post("/deepseek/ask")
def ask_deepseek(payload: Dict[str, Any] = Body(...)):
    prompt = payload.get("prompt", "Привет! Подтверди готовность.")
    res = test_deepseek_web(prompt)
    if res.get("status") == "error":
        raise HTTPException(status_code=500, detail=res.get("error"))
    return res
