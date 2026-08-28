"""Export/import shared finished archive + team_stats snapshot (.nbarchive.zip)."""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import zipfile
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from settings import settings

logger = logging.getLogger("archive_transfer")

NBARCHIVE_FORMAT_VERSION = 1
MODEL_DIR = os.getenv("MODEL_DIR", "/app/data/models")
TEAM_STATS_PATH = os.path.join(MODEL_DIR, "team_stats.json")
ARCHIVE_SQL_NAME = "finished_data.sql"

_ARCHIVE_TABLES = ("finished_events", "finished_bets", "finished_odds_history")


def _archive_dsn() -> str:
    return os.getenv("ARCHIVE_DATABASE_URL") or settings.DATABASE_URL


def _pg_env(dsn: str) -> dict[str, str]:
    parsed = urlparse(dsn)
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    return env


def _pg_base_cmd(dsn: str) -> list[str]:
    parsed = urlparse(dsn)
    host = parsed.hostname or "localhost"
    port = str(parsed.port or 5432)
    user = parsed.username or "autobet"
    db = (parsed.path or "/autobet").lstrip("/")
    return ["-h", host, "-p", port, "-U", user, "-d", db]


def _count_archive_rows() -> dict[str, int]:
    from database import get_archive_connection, release_connection

    counts: dict[str, int] = {}
    conn = get_archive_connection()
    try:
        with conn.cursor() as cur:
            for table in _ARCHIVE_TABLES:
                cur.execute(f"SELECT COUNT(*) AS n FROM finished.{table}")
                row = cur.fetchone()
                counts[table] = int(row["n"] if row else 0)
    finally:
        release_connection(conn)
    return counts


def export_archive_zip() -> tuple[bytes, str]:
    dsn = _archive_dsn()
    counts = _count_archive_rows()
    manifest: dict[str, Any] = {
        "format_version": NBARCHIVE_FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": os.getenv("NEUROBET_DEPLOY_MODE", "prod"),
        "counts": counts,
        "has_team_stats": os.path.isfile(TEAM_STATS_PATH),
    }

    dump_cmd = [
        "pg_dump",
        *_pg_base_cmd(dsn),
        "--schema=finished",
        "--data-only",
        "--no-owner",
        "--no-privileges",
    ]
    for table in _ARCHIVE_TABLES:
        dump_cmd.extend(["-t", f"finished.{table}"])

    proc = subprocess.run(
        dump_cmd,
        capture_output=True,
        env=_pg_env(dsn),
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")
        raise RuntimeError(f"pg_dump failed: {err.strip()}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr(ARCHIVE_SQL_NAME, proc.stdout)
        if os.path.isfile(TEAM_STATS_PATH):
            with open(TEAM_STATS_PATH, "rb") as f:
                zf.writestr("team_stats.json", f.read())

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"neurobet-archive-{stamp}.nbarchive.zip"
    return buf.getvalue(), filename


def import_archive_zip(data: bytes) -> dict[str, Any]:
    if not data:
        raise ValueError("Empty upload")

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        if "manifest.json" not in names:
            raise ValueError("Invalid .nbarchive.zip: missing manifest.json")
        if ARCHIVE_SQL_NAME not in names:
            raise ValueError(f"Invalid .nbarchive.zip: missing {ARCHIVE_SQL_NAME}")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        sql_bytes = zf.read(ARCHIVE_SQL_NAME)
        team_stats_bytes = zf.read("team_stats.json") if "team_stats.json" in names else None

    fmt = int(manifest.get("format_version") or 0)
    if fmt != NBARCHIVE_FORMAT_VERSION:
        raise ValueError(f"Unsupported archive format version: {fmt}")

    dsn = _archive_dsn()
    from database import get_archive_connection, release_connection

    conn = get_archive_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE finished.finished_odds_history, "
                "finished.finished_bets, finished.finished_events"
            )
        conn.commit()
    finally:
        release_connection(conn)

    psql_cmd = ["psql", *_pg_base_cmd(dsn), "-v", "ON_ERROR_STOP=1", "-q"]
    proc = subprocess.run(
        psql_cmd,
        input=sql_bytes,
        capture_output=True,
        env=_pg_env(dsn),
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")
        raise RuntimeError(f"psql import failed: {err.strip()}")

    if team_stats_bytes:
        os.makedirs(MODEL_DIR, exist_ok=True)
        tmp = TEAM_STATS_PATH + ".tmp"
        with open(tmp, "wb") as f:
            f.write(team_stats_bytes)
        os.replace(tmp, TEAM_STATS_PATH)

    counts = _count_archive_rows()
    return {
        "manifest": manifest,
        "counts": counts,
        "team_stats_imported": team_stats_bytes is not None,
    }
