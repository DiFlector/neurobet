from typing import List, Dict, Any

import time

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from app.config import DATABASE_URL

_pg_pool = psycopg2.pool.ThreadedConnectionPool(2, 20, dsn=DATABASE_URL)


def _checkout():
    last = None
    for _ in range(40):
        try:
            return _pg_pool.getconn()
        except pool.PoolError as e:
            last = e
            time.sleep(0.05)
    raise last


def get_connection():
    conn = _checkout()
    conn.cursor_factory = RealDictCursor
    with conn.cursor() as cur:
        cur.execute("SET search_path TO live, public")
    return conn


def get_finished_connection():
    conn = _checkout()
    conn.cursor_factory = RealDictCursor
    with conn.cursor() as cur:
        cur.execute("SET search_path TO finished, public")
    return conn


def release_connection(conn):
    _pg_pool.putconn(conn)


def save_ai_predictions(predictions: List[Dict[str, Any]], timestamp_str: str):
    conn = get_connection()
    cursor = conn.cursor()

    for p in predictions:
        cursor.execute("""
            INSERT INTO ai_predictions (
                event_id, factor_id, market_prefix, parameter,
                win_probability, error_rate, expected_roi,
                lightgbm_score, pytorch_score, predicted_win, decision_confidence, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(event_id, factor_id, parameter, market_prefix) DO UPDATE SET
                win_probability = excluded.win_probability,
                error_rate = excluded.error_rate,
                expected_roi = excluded.expected_roi,
                lightgbm_score = excluded.lightgbm_score,
                pytorch_score = excluded.pytorch_score,
                predicted_win = excluded.predicted_win,
                decision_confidence = excluded.decision_confidence,
                updated_at = excluded.updated_at;
        """, (
            p["event_id"], p["factor_id"], p.get("market_prefix", ""), str(p.get("parameter", "")),
            p["win_probability"], p["error_rate"], p["expected_roi"],
            p.get("lightgbm_score", 0.0), p.get("pytorch_score", 0.0),
            p.get("predicted_win"), p.get("decision_confidence"), timestamp_str
        ))

    conn.commit()
    release_connection(conn)
