from fastapi import APIRouter, HTTPException, Body
from typing import Optional, Dict, Any

from app.neuralbet import (
    run_neuralbet_inference_and_training,
    get_ai_settings,
    update_ai_settings,
    get_ai_logs,
    add_ai_log
)
from app.neuralbet import bankroll
from app.deepseek import test_deepseek_web

router = APIRouter()

@router.get("/health")
def health_check():
    return {"status": "ok", "service": "autobet-ai-microservice", "port": 8001}

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

@router.post("/deepseek/ask")
def ask_deepseek(payload: Dict[str, Any] = Body(...)):
    prompt = payload.get("prompt", "Привет! Подтверди готовность.")
    res = test_deepseek_web(prompt)
    if res.get("status") == "error":
        raise HTTPException(status_code=500, detail=res.get("error"))
    return res
