from fastapi import APIRouter, HTTPException, Body
from typing import Optional, Dict, Any

from app.neuralbet import (
    run_neuralbet_inference_and_training,
    get_ai_settings,
    update_ai_settings,
    get_ai_logs,
    add_ai_log
)
from app.deepseek import test_deepseek_web

router = APIRouter()

@router.get("/health")
def health_check():
    return {"status": "ok", "service": "autobet-ai-microservice", "port": 8001}

@router.post("/predict-and-train")
def predict_and_train():
    try:
        res = run_neuralbet_inference_and_training()
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

@router.post("/deepseek/ask")
def ask_deepseek(payload: Dict[str, Any] = Body(...)):
    prompt = payload.get("prompt", "Привет! Подтверди готовность.")
    res = test_deepseek_web(prompt)
    if res.get("status") == "error":
        raise HTTPException(status_code=500, detail=res.get("error"))
    return res
