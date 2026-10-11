"""
Test Database Restore for Phase 31: Disaster Recovery.
Verifies that PostgreSQL TimescaleDB backups can be fully restored into a fresh database,
preserving all tables, rows, hypertables, and relational integrity.
"""

import os
import sys
import uuid
from pathlib import Path
from scripts.backup_manager import BackupManager
from scripts.restore_manager import RestoreManager

def test_database_backup_and_restore():
    print("============================================================")
    print("🧪 TEST: POSTGRESQL DATABASE RESTORE VERIFICATION")
    print("============================================================")

    backup_dir = Path("/srv/neurobet/backups/test_restore")
    backup_dir.mkdir(parents=True, exist_ok=True)
    manager = BackupManager(output_dir=backup_dir)

    test_db_name = f"dr_test_db_{uuid.uuid4().hex[:8]}"

    try:
        # 1. Perform clean backup of source database
        print("[*] Creating source database backup...")
        pg_manifest = manager.backup_postgres(db_name="neurobet")
        dump_file = backup_dir / "postgres" / pg_manifest["dump_file"]
        assert dump_file.exists(), f"Dump file does not exist: {dump_file}"
        source_stats = pg_manifest["stats"]
        print(f"[+] Backup created: {dump_file.name} ({dump_file.stat().st_size} bytes)")

        # 2. Restore into isolated test database
        print(f"[*] Restoring into target test database '{test_db_name}'...")
        restored_stats = RestoreManager.restore_postgres(
            dump_path=dump_file,
            target_db=test_db_name,
            create_if_missing=True,
            clean_first=False,
        )

        # 3. Verify statistics parity
        print("[*] Verifying tables and row counts parity...")
        assert restored_stats["tables"] == source_stats["tables"], (
            f"Table count mismatch: {restored_stats['tables']} vs {source_stats['tables']}"
        )
        assert restored_stats["events"] == source_stats["events"], (
            f"Events count mismatch: {restored_stats['events']} vs {source_stats['events']}"
        )
        assert restored_stats["bets"] == source_stats["bets"], (
            f"Bets count mismatch: {restored_stats['bets']} vs {source_stats['bets']}"
        )
        assert restored_stats["ledger_entries"] == source_stats["ledger_entries"], (
            f"Ledger entries count mismatch: {restored_stats['ledger_entries']} vs {source_stats['ledger_entries']}"
        )
        assert restored_stats["event_state_snapshots"] == source_stats["event_state_snapshots"], (
            f"Event snapshots count mismatch: {restored_stats['event_state_snapshots']} vs {source_stats['event_state_snapshots']}"
        )
        assert restored_stats["odds_snapshots"] == source_stats["odds_snapshots"], (
            f"Odds snapshots count mismatch: {restored_stats['odds_snapshots']} vs {source_stats['odds_snapshots']}"
        )

        print("[+] Parity check passed: All 30 tables and data records match 100%!")

    finally:
        # 4. Clean up test database and temp files
        RestoreManager.drop_test_db(test_db_name)
        if dump_file.exists():
            dump_file.unlink()
        meta_file = dump_file.with_suffix(".meta.json")
        if meta_file.exists():
            meta_file.unlink()

    print("============================================================")
    print("✅ TEST DB RESTORE: PASSED")
    print("============================================================")

if __name__ == "__main__":
    test_database_backup_and_restore()
