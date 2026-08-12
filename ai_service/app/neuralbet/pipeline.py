import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.core.database import get_connection, get_finished_connection, save_ai_predictions
from app.neuralbet.model import NeuralBetEnsemble

logger = logging.getLogger("ai_service_pipeline")

ensemble_engine = NeuralBetEnsemble()

AI_SETTINGS = {
    "ai_enabled": True,
    "training_enabled": True
}

AI_LOGS: List[Dict[str, Any]] = []
MAX_LOG_ENTRIES = 300

def add_ai_log(category: str, message: str, level: str = "INFO"):
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

def run_neuralbet_inference_and_training() -> Dict[str, Any]:
    if not AI_SETTINGS["ai_enabled"]:
        add_ai_log("INFERENCE", "AI Inference skipped (Disabled by Admin).", level="WARNING")
        return {"status": "disabled", "predictions_count": 0, "finished_samples_trained": 0}

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 
            l.event_id, l.factor_id, l.market_prefix, l.label, l.parameter, l.coefficient,
            e.sport_path, e.match_name, e.score_1, e.score_2, e.timer
        FROM latest_odds l
        JOIN events e ON l.event_id = e.event_id
        WHERE e.is_live = 1
    """)
    live_odds_rows = cursor.fetchall()

    predictions = []
    timestamp_str = datetime.now().isoformat()

    for row in live_odds_rows:
        eid = row["event_id"]
        fid = row["factor_id"]
        prefix = row["market_prefix"] or ""
        param = str(row["parameter"] or "")
        coeff = float(row["coefficient"] or 1.0)
        s1 = int(row["score_1"] or 0)
        s2 = int(row["score_2"] or 0)

        cursor.execute("""
            SELECT coefficient 
            FROM odds_history 
            WHERE event_id = ? 
              AND factor_id = ? 
              AND COALESCE(parameter, '') = ? 
              AND COALESCE(market_prefix, '') = ?
            ORDER BY id ASC
        """, (eid, fid, param, prefix))
        
        hist_rows = cursor.fetchall()
        trajectory = [float(h["coefficient"]) for h in hist_rows] if hist_rows else [coeff]
        initial_coeff = trajectory[0] if trajectory else coeff

        win_prob, error_rate, lgb_score, torch_score = ensemble_engine.predict_single(
            odds_trajectory=trajectory,
            current_coeff=coeff,
            initial_coeff=initial_coeff,
            score_1=s1,
            score_2=s2
        )

        expected_roi = ((win_prob / 100.0) * coeff - 1.0) * 100.0

        predictions.append({
            "event_id": eid,
            "factor_id": fid,
            "market_prefix": prefix,
            "parameter": param,
            "win_probability": round(win_prob, 1),
            "error_rate": round(error_rate, 1),
            "expected_roi": round(expected_roi, 1),
            "lightgbm_score": round(lgb_score, 3),
            "pytorch_score": round(torch_score, 3)
        })

    if predictions:
        save_ai_predictions(predictions, timestamp_str)
        add_ai_log("INFERENCE", f"Evaluated predictions for {len(predictions)} active live outcomes. (PyTorch & LightGBM scores saved)")
    conn.close()

    if not AI_SETTINGS["training_enabled"]:
        add_ai_log("TRAINING", "Online Retraining skipped (Disabled by Admin).", level="WARNING")
        return {"predictions_count": len(predictions), "finished_samples_trained": 0}

    finished_rows = []
    try:
        f_conn = get_finished_connection()
        f_cursor = f_conn.cursor()
        f_cursor.execute("""
            SELECT f.event_id, f.score_1, f.score_2, h.factor_id, h.parameter, h.market_prefix, h.is_win
            FROM finished_events f
            JOIN finished_odds_history h ON f.event_id = h.event_id
            ORDER BY f.event_id DESC
            LIMIT 200
        """)
        finished_rows = f_cursor.fetchall()
        f_conn.close()
    except Exception as e:
        logger.error(f"Error querying finished training db: {e}")

    if finished_rows:
        training_samples = []
        for fr in finished_rows:
            training_samples.append({
                "score_diff": fr["score_1"] - fr["score_2"],
                "is_win": fr["is_win"],
                "odds_seq": [1.5] * 10
            })
        ensemble_engine.train_online(training_samples)
        add_ai_log("TRAINING", f"PyTorch AdamW gradient step completed on {len(finished_rows)} archived finished match samples.")
    else:
        add_ai_log("TRAINING", "No new finished matches in database for retraining step.")

    return {
        "predictions_count": len(predictions),
        "finished_samples_trained": len(finished_rows)
    }
