"""Export/import shared finished archive + team_stats snapshot (.nbarchive.zip)."""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from typing import Any, Optional
from urllib.parse import urlparse

from neurobet_time import now_moscow_iso, now_moscow_stamp

from settings import settings

logger = logging.getLogger("archive_transfer")

NBARCHIVE_FORMAT_VERSION = 1
MODEL_DIR = os.getenv("MODEL_DIR", "/app/data/models")
TEAM_STATS_PATH = os.path.join(MODEL_DIR, "team_stats.json")
ARCHIVE_SQL_NAME = "finished_data.sql"

_ARCHIVE_TABLES = ("finished_events", "finished_bets", "finished_odds_history")

# pg_dump 17 (Debian trixie client in backend image) emits SET lines PG16 servers reject.
_PG_DUMP_PG17_ONLY_PREFIXES = (
    "SET transaction_timeout",
    "\\restrict",
    "\\unrestrict",
)


def _sanitize_pg_dump_sql(sql_bytes: bytes) -> bytes:
    """Make pg_dump output importable on PostgreSQL 16 (and older psql clients)."""
    try:
        text = sql_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return sql_bytes
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if any(stripped.startswith(prefix) for prefix in _PG_DUMP_PG17_ONLY_PREFIXES):
            continue
        kept.append(line)
    return "".join(kept).encode("utf-8")


def _sanitize_pg_dump_sql_file(sql_path: str) -> None:
    """Stream-sanitize a SQL dump file in place (low memory vs loading whole dump)."""
    tmp_path = sql_path + ".sanitized"
    with open(sql_path, "rb") as src, open(tmp_path, "wb") as dst:
        for line in src:
            try:
                stripped = line.decode("utf-8").strip()
            except UnicodeDecodeError:
                dst.write(line)
                continue
            if any(
                stripped.startswith(prefix)
                for prefix in _PG_DUMP_PG17_ONLY_PREFIXES
            ):
                continue
            dst.write(line)
    os.replace(tmp_path, sql_path)


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
        "exported_at": now_moscow_iso(),
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

    sql_bytes = _sanitize_pg_dump_sql(proc.stdout)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr(ARCHIVE_SQL_NAME, sql_bytes)
        if os.path.isfile(TEAM_STATS_PATH):
            with open(TEAM_STATS_PATH, "rb") as f:
                zf.writestr("team_stats.json", f.read())

    stamp = now_moscow_stamp()
    filename = f"neurobet-archive-{stamp}.nbarchive.zip"
    return buf.getvalue(), filename


def _import_finished_sql(dsn: str, sql_path: str, work_dir: str) -> None:
    """Import pg_dump SQL with table locks and duplicate-id tolerance on finished_bets."""
    prelude_path = os.path.join(work_dir, "import-prelude.sql")
    postlude_path = os.path.join(work_dir, "import-postlude.sql")
    with open(prelude_path, "w", encoding="utf-8") as f:
        f.write(
            """
BEGIN;
LOCK TABLE finished.finished_events, finished.finished_bets, finished.finished_odds_history
    IN ACCESS EXCLUSIVE MODE;
TRUNCATE finished.finished_odds_history, finished.finished_bets, finished.finished_events
    RESTART IDENTITY;
-- pg_dump COPY may repeat surrogate ids; drop the unique index until post-import dedupe.
DROP INDEX IF EXISTS finished.idx_finished_bets_id;
"""
        )
    with open(postlude_path, "w", encoding="utf-8") as f:
        f.write(
            """
DELETE FROM finished.finished_bets a
USING finished.finished_bets b
WHERE a.id = b.id AND a.ctid > b.ctid;
CREATE UNIQUE INDEX IF NOT EXISTS idx_finished_bets_id ON finished.finished_bets (id);
COMMIT;
"""
        )

    logger.info("Archive import: running psql on %s", sql_path)
    proc = subprocess.run(
        [
            "psql",
            *_pg_base_cmd(dsn),
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
            "-f",
            prelude_path,
            "-f",
            sql_path,
            "-f",
            postlude_path,
        ],
        capture_output=True,
        env=_pg_env(dsn),
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")
        tail = err.strip()
        if len(tail) > 2000:
            tail = tail[-2000:]
        raise RuntimeError(f"psql import failed: {tail}")


def import_archive_zip_file(zip_path: str) -> dict[str, Any]:
    """Import from a .nbarchive.zip on disk (memory-safe for large archives)."""
    if not zip_path or not os.path.isfile(zip_path):
        raise ValueError("Файл архива не найден")

    size = os.path.getsize(zip_path)
    if size <= 0:
        raise ValueError("Пустой файл")

    staging_root = os.path.dirname(MODEL_DIR) or MODEL_DIR
    work_dir = tempfile.mkdtemp(prefix="nbarchive-", dir=staging_root)
    team_stats_path: Optional[str] = None
    try:
        try:
            zf = zipfile.ZipFile(zip_path)
        except zipfile.BadZipFile:
            raise ValueError("Файл не является корректным .zip / .nbarchive.zip")

        with zf:
            names = set(zf.namelist())
            if "manifest.json" not in names:
                raise ValueError("Некорректный .nbarchive.zip: нет manifest.json")
            if ARCHIVE_SQL_NAME not in names:
                raise ValueError(
                    f"Некорректный .nbarchive.zip: нет {ARCHIVE_SQL_NAME} "
                    "(возможно, выбран .nbmodel.zip вместо архива обучения)"
                )
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            zf.extract(ARCHIVE_SQL_NAME, work_dir)
            if "team_stats.json" in names:
                zf.extract("team_stats.json", work_dir)
                team_stats_path = os.path.join(work_dir, "team_stats.json")

        fmt = int(manifest.get("format_version") or 0)
        if fmt != NBARCHIVE_FORMAT_VERSION:
            raise ValueError(f"Неподдерживаемая версия архива: {fmt}")

        sql_path = os.path.join(work_dir, ARCHIVE_SQL_NAME)
        logger.info(
            "Archive import: zip=%d bytes, sql_extracted=%d bytes",
            size,
            os.path.getsize(sql_path),
        )
        _sanitize_pg_dump_sql_file(sql_path)

        dsn = _archive_dsn()
        _import_finished_sql(dsn, sql_path, work_dir)

        if team_stats_path and os.path.isfile(team_stats_path):
            os.makedirs(MODEL_DIR, exist_ok=True)
            tmp = TEAM_STATS_PATH + ".tmp"
            shutil.copyfile(team_stats_path, tmp)
            os.replace(tmp, TEAM_STATS_PATH)

        counts = _count_archive_rows()
        logger.info("Archive import finished: %s", counts)
        return {
            "manifest": manifest,
            "counts": counts,
            "team_stats_imported": team_stats_path is not None,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def import_archive_zip(data: bytes) -> dict[str, Any]:
    if not data:
        raise ValueError("Пустой файл")

    staging_root = os.path.dirname(MODEL_DIR) or MODEL_DIR
    zip_tmp = tempfile.NamedTemporaryFile(
        prefix="nbarchive-upload-",
        suffix=".nbarchive.zip",
        delete=False,
        dir=staging_root,
    )
    zip_path = zip_tmp.name
    try:
        zip_tmp.write(data)
        zip_tmp.close()
        return import_archive_zip_file(zip_path)
    finally:
        try:
            os.unlink(zip_path)
        except OSError:
            pass
