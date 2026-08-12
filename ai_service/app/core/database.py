import sqlite3
import os
from typing import List, Dict, Any
from app.config import DB_PATH, FINISHED_DB_PATH

def py_lower(val: Any) -> str:
    if val is None:
        return ""
    return str(val).lower()

def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.create_function("py_lower", 1, py_lower)
    return conn

def get_finished_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(FINISHED_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(FINISHED_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.create_function("py_lower", 1, py_lower)
    return conn

def save_ai_predictions(predictions: List[Dict[str, Any]], timestamp_str: str):
    conn = get_connection()
    cursor = conn.cursor()

    for p in predictions:
        cursor.execute("""
            INSERT INTO ai_predictions (
                event_id, factor_id, market_prefix, parameter,
                win_probability, error_rate, expected_roi,
                lightgbm_score, pytorch_score, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, factor_id, parameter, market_prefix) DO UPDATE SET
                win_probability = excluded.win_probability,
                error_rate = excluded.error_rate,
                expected_roi = excluded.expected_roi,
                lightgbm_score = excluded.lightgbm_score,
                pytorch_score = excluded.pytorch_score,
                updated_at = excluded.updated_at;
        """, (
            p["event_id"], p["factor_id"], p.get("market_prefix", ""), str(p.get("parameter", "")),
            p["win_probability"], p["error_rate"], p["expected_roi"],
            p.get("lightgbm_score", 0.0), p.get("pytorch_score", 0.0), timestamp_str
        ))

    conn.commit()
    conn.close()
