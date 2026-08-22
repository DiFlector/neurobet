from contextlib import contextmanager
from typing import List, Dict, Any

import time

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from app.config import DATABASE_URL

# Admin/MCP polls (coverage COUNT, bankroll) run outside _engine_lock and used to
# stampede this pool while a cold-start fetch already held a connection — every
# inference cycle then died with "connection pool exhausted". Cap stays modest;
# SafeConn + single-flight in pipeline.py are the real guards.
_POOL_MIN = 2
_POOL_MAX = 32
_pg_pool = psycopg2.pool.ThreadedConnectionPool(_POOL_MIN, _POOL_MAX, dsn=DATABASE_URL)


class _SafeConn:
    """Returns the connection to the pool on GC if release_connection was skipped."""

    __slots__ = ("_conn", "_released", "_pool")

    def __init__(self, conn, pg_pool):
        self._conn = conn
        self._pool = pg_pool
        self._released = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def _release(self):
        if self._released:
            return
        self._released = True
        try:
            self._conn.rollback()
        except Exception:
            pass
        try:
            self._pool.putconn(self._conn)
        except Exception:
            pass

    def __del__(self):
        self._release()


def _pool_status() -> str:
    try:
        used = len(getattr(_pg_pool, "_used", {}) or {})
        idle = len(getattr(_pg_pool, "_pool", []) or [])
        return f"{used} used / {idle} idle / max {_POOL_MAX}"
    except Exception:
        return f"max {_POOL_MAX}"


def _checkout():
    last = None
    for _ in range(20):
        try:
            return _pg_pool.getconn()
        except pool.PoolError as e:
            last = e
            time.sleep(0.05)
    raise pool.PoolError(
        f"connection pool exhausted ({_pool_status()})"
    ) from last


def _wrap(raw, schema: str):
    try:
        raw.cursor_factory = RealDictCursor
        with raw.cursor() as cur:
            cur.execute(f"SET search_path TO {schema}, public")
        return _SafeConn(raw, _pg_pool)
    except Exception:
        try:
            _pg_pool.putconn(raw)
        except Exception:
            pass
        raise


def get_connection():
    return _wrap(_checkout(), "live")


def get_finished_connection():
    return _wrap(_checkout(), "finished")


def release_connection(conn):
    if isinstance(conn, _SafeConn):
        conn._release()
        return
    _pg_pool.putconn(conn)


@contextmanager
def db_connection(schema="live"):
    conn = get_finished_connection() if schema == "finished" else get_connection()
    try:
        yield conn
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        release_connection(conn)


def save_ai_predictions(predictions: List[Dict[str, Any]], timestamp_str: str):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        for p in predictions:
            cursor.execute("""
                INSERT INTO ai_predictions (
                    event_id, factor_id, market_prefix, parameter,
                    win_probability, error_rate, expected_roi,
                    lightgbm_score, pytorch_score, predicted_win, decision_confidence,
                    updated_at
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
                p.get("predicted_win"), p.get("decision_confidence"),
                timestamp_str
            ))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        release_connection(conn)
