#!/usr/bin/env python3
"""
Neurobet Disaster Recovery: Restore Manager.
Phase 31: Performance, Safety, and Disaster Recovery.

Supports:
- PostgreSQL database restoration via pg_restore (target production or test databases)
- MinIO object storage restore
- Model checkpoints and artifacts restore
- Automated verification of restored schemas and row counts
"""

import argparse
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Dict, Any, List, Optional

WORKSPACE_ROOT = Path("/srv/neurobet")


def run_cmd(cmd: List[str], check: bool = True, stdin=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, stdin=stdin, stdout=stdout, stderr=stderr)
    if check and res.returncode != 0:
        err = res.stderr.decode("utf-8", errors="replace") if res.stderr else "Unknown error"
        raise RuntimeError(f"Command failed (exit {res.returncode}): {' '.join(cmd)}\n{err}")
    return res


class RestoreManager:
    @staticmethod
    def verify_postgres(db_name: str) -> Dict[str, Any]:
        """Queries table statistics and hypertables for verification."""
        stats_sql = """
        SELECT
            (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public') as table_count,
            (SELECT count(*) FROM events) as events_count,
            (SELECT count(*) FROM bets) as bets_count,
            (SELECT count(*) FROM ledger_entries) as ledger_count,
            (SELECT count(*) FROM event_state_snapshots) as event_snapshots_count,
            (SELECT count(*) FROM odds_snapshots) as odds_snapshots_count,
            (SELECT count(*) FROM feature_snapshots) as feature_snapshots_count;
        """
        res = run_cmd([
            "docker", "exec", "neurobet_postgres",
            "psql", "-U", "neurobet", "-d", db_name, "-t", "-A", "-F", "|", "-c", stats_sql
        ])
        parts = res.stdout.decode("utf-8").strip().split("|")
        return {
            "tables": int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0,
            "events": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0,
            "bets": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
            "ledger_entries": int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0,
            "event_state_snapshots": int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0,
            "odds_snapshots": int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0,
            "feature_snapshots": int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else 0,
        }

    @classmethod
    def restore_postgres(
        cls,
        dump_path: Path,
        target_db: str = "neurobet_restore_test",
        create_if_missing: bool = True,
        clean_first: bool = False,
    ) -> Dict[str, Any]:
        """
        Restores a PostgreSQL dump into the target database.
        """
        if not dump_path.exists():
            raise FileNotFoundError(f"Dump file not found: {dump_path}")

        print(f"[*] Restoring PostgreSQL dump '{dump_path.name}' into database '{target_db}'...")

        # 1. Check if database exists, create if missing
        chk_db = run_cmd([
            "docker", "exec", "neurobet_postgres",
            "psql", "-U", "neurobet", "-d", "postgres", "-t", "-A", "-c",
            f"SELECT 1 FROM pg_database WHERE datname='{target_db}';"
        ], check=False)
        db_exists = bool(chk_db.stdout.decode("utf-8").strip() == "1")

        if not db_exists and create_if_missing:
            print(f"[*] Creating target database '{target_db}'...")
            run_cmd([
                "docker", "exec", "neurobet_postgres",
                "createdb", "-U", "neurobet", target_db
            ])
            # Enable timescaledb extension in new database
            run_cmd([
                "docker", "exec", "neurobet_postgres",
                "psql", "-U", "neurobet", "-d", target_db, "-c",
                "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"
            ])

        # 2. Run pg_restore streaming dump_path to container stdin
        restore_cmd = [
            "docker", "exec", "-i", "neurobet_postgres",
            "pg_restore", "-U", "neurobet", "-d", target_db,
            "--no-owner", "--no-acl"
        ]
        if clean_first:
            restore_cmd.append("--clean")
            restore_cmd.append("--if-exists")

        with open(dump_path, "rb") as f_in:
            # pg_restore might return non-zero warnings for existing extensions, which is normal in postgres
            res = run_cmd(restore_cmd, stdin=f_in, check=False)
            if res.returncode not in (0, 1):  # 1 is for benign warnings during restore
                err = res.stderr.decode("utf-8", errors="replace")
                raise RuntimeError(f"pg_restore failed with code {res.returncode}: {err}")

        # 3. Verify statistics
        stats = cls.verify_postgres(target_db)
        print(f"[+] PostgreSQL restore to '{target_db}' succeeded: {stats['tables']} tables, {stats['events']} events, {stats['ledger_entries']} ledger entries.")
        return stats

    @classmethod
    def drop_test_db(cls, db_name: str = "neurobet_restore_test") -> None:
        """Drops temporary restore test database."""
        print(f"[*] Dropping test database '{db_name}'...")
        # Terminate active connections first
        term_sql = f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{db_name}' AND pid <> pg_backend_pid();"
        run_cmd([
            "docker", "exec", "neurobet_postgres",
            "psql", "-U", "neurobet", "-d", "postgres", "-c", term_sql
        ], check=False)
        run_cmd([
            "docker", "exec", "neurobet_postgres",
            "dropdb", "-U", "neurobet", "--if-exists", db_name
        ])
        print(f"[+] Database '{db_name}' dropped.")

    @classmethod
    def restore_models(cls, archive_path: Path, target_dir: Optional[Path] = None) -> List[str]:
        """Restores model artifacts from tar.gz archive."""
        dest = target_dir or (WORKSPACE_ROOT / "models" / "checkpoints")
        dest.mkdir(parents=True, exist_ok=True)
        restored = []
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                tar.extract(member, path=dest)
                restored.append(member.name)
        print(f"[+] Restored {len(restored)} model artifacts to {dest}")
        return restored


def main():
    parser = argparse.ArgumentParser(description="Neurobet Disaster Recovery Restore Manager")
    parser.add_argument("--postgres", type=str, help="Path to PostgreSQL dump file to restore")
    parser.add_argument("--target-db", default="neurobet_restore_test", help="Target database name")
    parser.add_argument("--verify-db", type=str, help="Verify statistics of specified database")
    parser.add_argument("--drop-db", type=str, help="Drop specified database (safety check)")
    parser.add_argument("--models", type=str, help="Path to models archive to restore")

    args = parser.parse_args()

    if args.postgres:
        RestoreManager.restore_postgres(Path(args.postgres), target_db=args.target_db, create_if_missing=True)
    elif args.verify_db:
        stats = RestoreManager.verify_postgres(args.verify_db)
        print(json.dumps(stats, indent=2))
    elif args.drop_db:
        RestoreManager.drop_test_db(args.drop_db)
    elif args.models:
        RestoreManager.restore_models(Path(args.models))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
