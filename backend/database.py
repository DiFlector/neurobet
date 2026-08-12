import sqlite3
import json
import os
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from settings import settings

logger = logging.getLogger("database")

DB_PATH = settings.DATABASE_PATH

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

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Events table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id INTEGER PRIMARY KEY,
            sport_id INTEGER,
            sport_path TEXT,
            match_name TEXT,
            team_1 TEXT,
            team_2 TEXT,
            score_1 INTEGER,
            score_2 INTEGER,
            score TEXT,
            timer TEXT,
            is_live INTEGER DEFAULT 1,
            sub_markets_json TEXT,
            total_odds_count INTEGER DEFAULT 0,
            last_updated_at TEXT
        );
    """)

    # Odds History table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS odds_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER,
            factor_id INTEGER,
            market_prefix TEXT,
            label TEXT,
            parameter TEXT,
            coefficient REAL,
            score_at_time TEXT,
            timestamp TEXT
        );
    """)

    # Latest Odds table (for quick retrieval of current odds per event)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS latest_odds (
            event_id INTEGER,
            factor_id INTEGER,
            market_prefix TEXT,
            label TEXT,
            parameter TEXT,
            coefficient REAL,
            initial_coefficient REAL,
            score_at_time TEXT,
            updated_at TEXT,
            PRIMARY KEY (event_id, factor_id, parameter, market_prefix)
        );
    """)

    try:
        cursor.execute("ALTER TABLE latest_odds ADD COLUMN initial_coefficient REAL;")
    except Exception:
        pass

    # Indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_odds_hist_event ON odds_history (event_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_odds_hist_lookup ON odds_history (event_id, factor_id, parameter, market_prefix);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_odds_hist_ts ON odds_history (timestamp);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_sport ON events (sport_path);")
    
    conn.commit()
    conn.close()
    logger.info(f"Database initialized successfully at {DB_PATH}")

def save_parsed_events(parsed_events: List[Dict[str, Any]], timestamp_str: str):
    conn = get_connection()
    cursor = conn.cursor()

    active_event_ids = [e["event_id"] for e in parsed_events]

    # Mark non-active live events as is_live = 0 if they disappeared
    if active_event_ids:
        placeholders = ",".join("?" for _ in active_event_ids)
        cursor.execute(f"UPDATE events SET is_live = 0 WHERE event_id NOT IN ({placeholders}) AND is_live = 1", active_event_ids)
    else:
        cursor.execute("UPDATE events SET is_live = 0 WHERE is_live = 1")

    for ev in parsed_events:
        eid = ev["event_id"]
        sub_markets_json = json.dumps(ev.get("sub_markets", []), ensure_ascii=False)

        cursor.execute("""
            INSERT INTO events (
                event_id, sport_id, sport_path, match_name, team_1, team_2,
                score_1, score_2, score, timer, is_live, sub_markets_json,
                total_odds_count, last_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                sport_id = excluded.sport_id,
                sport_path = excluded.sport_path,
                match_name = excluded.match_name,
                team_1 = excluded.team_1,
                team_2 = excluded.team_2,
                score_1 = excluded.score_1,
                score_2 = excluded.score_2,
                score = excluded.score,
                timer = excluded.timer,
                is_live = 1,
                sub_markets_json = excluded.sub_markets_json,
                total_odds_count = excluded.total_odds_count,
                last_updated_at = excluded.last_updated_at;
        """, (
            eid, ev.get("sport_id"), ev.get("sport_path"), ev.get("match_name"),
            ev.get("team_1"), ev.get("team_2"), ev.get("score_1", 0), ev.get("score_2", 0),
            ev.get("score", "0:0"), ev.get("timer", ""), sub_markets_json,
            ev.get("total_odds_count", 0), timestamp_str
        ))

        # Insert odds history & update latest odds
        for odd in ev.get("odds", []):
            fid = odd["factor_id"]
            prefix = odd.get("market_prefix", "")
            param = str(odd.get("parameter", "")) if odd.get("parameter") is not None else ""
            coeff = float(odd.get("coefficient", 0.0))
            label = odd.get("label", "")
            score_str = ev.get("score", "0:0")

            # Save historical record
            cursor.execute("""
                INSERT INTO odds_history (
                    event_id, factor_id, market_prefix, label, parameter, coefficient, score_at_time, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (eid, fid, prefix, label, param, coeff, score_str, timestamp_str))

            # Upsert latest odds (preserves initial_coefficient on conflict)
            cursor.execute("""
                INSERT INTO latest_odds (
                    event_id, factor_id, market_prefix, label, parameter, coefficient, initial_coefficient, score_at_time, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, factor_id, parameter, market_prefix) DO UPDATE SET
                    coefficient = excluded.coefficient,
                    score_at_time = excluded.score_at_time,
                    updated_at = excluded.updated_at;
            """, (eid, fid, prefix, label, param, coeff, coeff, score_str, timestamp_str))

    conn.commit()
    conn.close()

def get_live_matches(sport_filter: Optional[str] = None, search: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM events WHERE is_live = 1"
    params = []

    if sport_filter and sport_filter.lower() != "all":
        query += " AND py_lower(sport_path) LIKE ?"
        params.append(f"%{sport_filter.lower()}%")

    if search:
        query += " AND (py_lower(match_name) LIKE ? OR py_lower(team_1) LIKE ? OR py_lower(team_2) LIKE ?)"
        s_param = f"%{search.lower()}%"
        params.extend([s_param, s_param, s_param])

    query += " ORDER BY sport_path ASC, event_id DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()

    result = []
    for r in rows:
        match_dict = dict(r)
        eid = match_dict["event_id"]
        match_dict["sub_markets"] = json.loads(match_dict.get("sub_markets_json") or "[]")

        # Fetch current latest odds for this match with initial_coefficient from history
        cursor.execute("""
            SELECT 
                l.factor_id, 
                l.market_prefix, 
                l.label, 
                l.parameter, 
                l.coefficient, 
                COALESCE(
                    (
                        SELECT h.coefficient 
                        FROM odds_history h 
                        WHERE h.event_id = l.event_id 
                          AND h.factor_id = l.factor_id 
                          AND COALESCE(h.parameter, '') = COALESCE(l.parameter, '') 
                          AND COALESCE(h.market_prefix, '') = COALESCE(l.market_prefix, '') 
                        ORDER BY h.id ASC 
                        LIMIT 1
                    ), 
                    l.coefficient
                ) AS initial_coefficient, 
                l.score_at_time 
            FROM latest_odds l 
            WHERE l.event_id = ?
        """, (eid,))
        match_dict["odds"] = [dict(o) for o in cursor.fetchall()]
        result.append(match_dict)

    conn.close()
    return result

def get_odds_history(event_id: int, factor_id: int, parameter: Optional[str] = None, market_prefix: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT id, event_id, factor_id, market_prefix, label, parameter, coefficient, score_at_time, timestamp FROM odds_history WHERE event_id = ? AND factor_id = ?"
    params = [event_id, factor_id]

    if parameter is not None:
        query += " AND parameter = ?"
        params.append(parameter)

    if market_prefix is not None:
        query += " AND market_prefix = ?"
        params.append(market_prefix)

    query += " ORDER BY id ASC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [dict(r) for r in rows]

def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

def get_db_stats() -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM events WHERE is_live = 1")
    live_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM events")
    total_events = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM odds_history")
    history_count = cursor.fetchone()[0]

    cursor.execute("SELECT MAX(last_updated_at) FROM events")
    last_updated = cursor.fetchone()[0]

    conn.close()

    db_size_bytes = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0

    return {
        "live_events_count": live_count,
        "total_events_count": total_events,
        "total_odds_history_count": history_count,
        "last_updated_at": last_updated,
        "db_size_bytes": db_size_bytes,
        "db_size_formatted": format_file_size(db_size_bytes)
    }
