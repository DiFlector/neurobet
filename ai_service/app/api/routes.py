import logging
import threading

from fastapi import APIRouter, HTTPException, Body, Query, UploadFile, File
from fastapi.responses import Response
from typing import Optional, Dict, Any

logger = logging.getLogger("ai_service_routes")

_cycle_lock = threading.Lock()
_cycle_in_flight = False

from app.config import get_capabilities, IS_PROD, IS_DEV
from app.neuralbet import (
    run_neuralbet_inference_and_training,
    get_ai_settings,
    update_ai_settings,
    get_ai_logs,
    add_ai_log,
    reset_neural_network,
    create_new_model_and_cold_start,
    get_reset_progress,
    get_training_health,
    run_backtest,
    get_backtest_history,
    get_latest_backtest,
    get_backtest_progress,
    BACKTEST_DEFAULT_LIMIT,
    BACKTEST_MAX_LIMIT,
    get_training_history,
    reload_ensemble_checkpoints,
)
from app.neuralbet import bankroll
from app.neuralbet import model_registry

router = APIRouter()


def _require_dev(feature: str) -> None:
    if IS_PROD:
        raise HTTPException(
            status_code=403,
            detail=f"{feature} is not available on prod deploy mode",
        )


@router.get("/capabilities")
def read_capabilities():
    return {"status": "success", **get_capabilities()}

@router.get("/health")
def health_check():
    return {"status": "ok", "service": "neurobet-ai-microservice", "port": 8001}


@router.post("/internal/reload-team-stats")
def reload_team_stats_route(payload: Dict[str, Any] = Body(default={})):
    from app.neuralbet.pipeline import reload_team_stats_cache

    force_db = bool(payload.get("force_db"))
    info = reload_team_stats_cache(force_db=force_db)
    return {"status": "success", **info}


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
    if IS_PROD and payload.get("training_enabled") is True:
        raise HTTPException(status_code=403, detail="training_enabled cannot be enabled on prod")
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
    _require_dev("reset-model")
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
    _require_dev("backtest")
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


@router.get("/active-model")
def read_active_model():
    """Public read-only: name of the model used for live inference."""
    model = model_registry.get_public_active_model()
    return {"status": "success", "model": model}


@router.get("/models")
def list_registered_models():
    model_registry.bootstrap_legacy_if_needed()
    active = model_registry.get_active_models()
    return {
        "status": "success",
        "models": model_registry.list_models(),
        "active": model_registry.get_active_model(),
        "active_models": active,
    }


@router.post("/models/new")
def create_new_model_route(payload: Dict[str, Any] = Body(default={})):
    _require_dev("new model")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        result = create_new_model_and_cold_start(name)
        return {"status": "success", **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("New model failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models/upload")
async def upload_model(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    try:
        manifest = model_registry.import_nbmodel_zip(data)
        add_ai_log("SYSTEM", f"Model imported: {manifest.get('name')} ({manifest.get('slug')})")
        return {"status": "success", "model": manifest}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Model upload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models/{slug}/activate")
def activate_registered_model(slug: str, payload: Dict[str, Any] = Body(default={})):
    slot = int(payload.get("slot") or 1)
    try:
        result = model_registry.activate_model(slug, reload_ensemble_checkpoints, slot=slot)
        add_ai_log(
            "SYSTEM",
            f"Active model slot {slot} switched to {result.get('name')} ({slug})",
        )
        return {"status": "success", "model": result, "active_models": model_registry.get_active_models()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Model activate failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models/slot/{slot}/deactivate")
def deactivate_model_slot(slot: int):
    try:
        result = model_registry.deactivate_slot(slot, reload_ensemble_checkpoints)
        add_ai_log("SYSTEM", f"Deactivated model slot {slot}")
        return {"status": "success", "active_models": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Model slot deactivate failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/models/active/group-name")
def set_active_group_name(payload: Dict[str, Any] = Body(default={})):
    group_name = str(payload.get("group_name") or "").strip()
    try:
        result = model_registry.set_group_name(group_name or None)
        return {"status": "success", "active_models": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/models/{slug}")
def delete_registered_model(slug: str):
    try:
        model_registry.delete_model(slug)
        add_ai_log("SYSTEM", f"Model removed from registry: {slug}")
        return {"status": "success", "slug": slug}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/models/export")
def export_model(slug: Optional[str] = Query(None)):
    _require_dev("model export")
    try:
        data, filename = model_registry.export_nbmodel_zip(slug=slug)
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/models/export-current")
def export_current_model_route(payload: Dict[str, Any] = Body(default={})):
    _require_dev("model export")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        data, filename = model_registry.export_current_model(name)
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Export current model failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models/export-group")
def export_model_group():
    try:
        data, filename = model_registry.export_model_group()
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/models/upload-group")
async def upload_model_group(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    try:
        result = model_registry.import_model_group(data)
        reload_ensemble_checkpoints()
        add_ai_log(
            "SYSTEM",
            f"Model group imported: {result.get('group_name')} "
            f"({', '.join(s['slug'] for s in result.get('slots') or [])})",
        )
        return {
            "status": "success",
            "group": result,
            "active_models": model_registry.get_active_models(),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Model group upload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

