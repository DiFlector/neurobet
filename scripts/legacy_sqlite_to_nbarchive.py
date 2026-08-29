#!/usr/bin/env python3
"""
Build .nbarchive.zip from legacy SQLite autobet_finished.db (old NeuroBet stack).

Usage on the OLD server (or copy the .db file here):
  python3 scripts/legacy_sqlite_to_nbarchive.py \\
    --sqlite /path/to/autobet_finished.db \\
    --team-stats /path/to/team_stats.json \\
    --out neurobet-archive-from-legacy.nbarchive.zip

Import on the NEW server:
  Admin → Архив обучения → Импорт .nbarchive.zip
  or: curl -X POST .../api/admin/archive/import -F file=@...
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import zipfile
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_shared = _root / "shared"
if (_shared / "neurobet_time").is_dir():
    import sys
    if str(_shared) not in sys.path:
        sys.path.insert(0, str(_shared))

from neurobet_time import now_moscow_iso

NBARCHIVE_FORMAT_VERSION = 1
ARCHIVE_SQL_NAME = "finished_data.sql"
TABLES = ("finished_events", "finished_bets", "finished_odds_history")


def _sql_literal(val) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val).replace("'", "''")
    return f"'{s}'"


def _fmt_copy_field(val) -> str:
    if val is None:
        return "\\N"
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val)
    return (
        s.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _export_table(conn: sqlite3.Connection, table: str) -> tuple[str, int]:
    cur = conn.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description or []]
    if not cols:
        return "", 0
    lines = [
        f"-- legacy sqlite: {table}",
        f"COPY finished.{table} ({', '.join(cols)}) FROM stdin;",
    ]
    count = 0
    for row in cur:
        lines.append("\t".join(_fmt_copy_field(v) for v in row))
        count += 1
    lines.append("\\.")
    lines.append("")
    return "\n".join(lines), count


def _export_table_inserts(conn: sqlite3.Connection, table: str) -> tuple[str, int]:
    """Fallback when COPY tab format breaks on special characters."""
    cur = conn.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description or []]
    if not cols:
        return "", 0
    lines = [f"-- legacy sqlite: {table}"]
    count = 0
    col_sql = ", ".join(cols)
    for row in cur:
        vals = ", ".join(_sql_literal(v) for v in row)
        lines.append(f"INSERT INTO finished.{table} ({col_sql}) VALUES ({vals});")
        count += 1
    lines.append("")
    return "\n".join(lines), count


def build_nbarchive(
    sqlite_path: Path,
    team_stats_path: Path | None,
    out_path: Path,
    use_copy: bool = True,
) -> dict:
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    try:
        sql_parts = [
            "SET search_path TO finished, public;",
            "",
        ]
        counts: dict[str, int] = {}
        for table in TABLES:
            try:
                conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
            except sqlite3.OperationalError:
                counts[table] = 0
                continue
            if use_copy:
                chunk, n = _export_table(conn, table)
            else:
                chunk, n = _export_table_inserts(conn, table)
            counts[table] = n
            if chunk:
                sql_parts.append(chunk)
        sql_bytes = "\n".join(sql_parts).encode("utf-8")
    finally:
        conn.close()

    manifest = {
        "format_version": NBARCHIVE_FORMAT_VERSION,
        "exported_at": now_moscow_iso(),
        "source": "legacy-sqlite",
        "counts": {
            "finished_events": counts.get("finished_events", 0),
            "finished_bets": counts.get("finished_bets", 0),
            "finished_odds_history": counts.get("finished_odds_history", 0),
        },
        "has_team_stats": team_stats_path is not None and team_stats_path.is_file(),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr(ARCHIVE_SQL_NAME, sql_bytes)
        if team_stats_path and team_stats_path.is_file():
            zf.write(team_stats_path, "team_stats.json")

    return manifest


def main() -> None:
    p = argparse.ArgumentParser(description="Convert legacy autobet_finished.db to .nbarchive.zip")
    p.add_argument("--sqlite", required=True, type=Path, help="Path to autobet_finished.db")
    p.add_argument("--team-stats", type=Path, default=None, help="Optional team_stats.json")
    p.add_argument("--out", type=Path, default=Path("neurobet-archive-from-legacy.nbarchive.zip"))
    p.add_argument("--inserts", action="store_true", help="Use INSERT instead of COPY (slower, safer)")
    args = p.parse_args()
    if not args.sqlite.is_file():
        raise SystemExit(f"SQLite file not found: {args.sqlite}")
    manifest = build_nbarchive(
        args.sqlite,
        args.team_stats,
        args.out,
        use_copy=not args.inserts,
    )
    print(json.dumps({"out": str(args.out.resolve()), "manifest": manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
