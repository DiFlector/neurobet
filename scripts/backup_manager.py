#!/usr/bin/env python3
"""
Neurobet Disaster Recovery: Automated Backup & Export Manager.
Phase 31: Performance, Safety, and Disaster Recovery.

Supports:
- PostgreSQL (TimescaleDB) pg_dump with metadata & checksums
- MinIO object storage archive & manifest
- Model artifacts packaging & registry state
- System and application configuration export
- Full Disaster Recovery bundle orchestration
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

WORKSPACE_ROOT = Path("/srv/neurobet")
DEFAULT_BACKUP_DIR = WORKSPACE_ROOT / "backups"


def utc_timestamp_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def compute_sha256(filepath: Path) -> str:
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def run_cmd(cmd: List[str], check: bool = True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, stdout=stdout, stderr=stderr)
    if check and res.returncode != 0:
        err = res.stderr.decode("utf-8", errors="replace") if res.stderr else "Unknown error"
        raise RuntimeError(f"Command failed (exit {res.returncode}): {' '.join(cmd)}\n{err}")
    return res


class BackupManager:
    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or DEFAULT_BACKUP_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.postgres_dir = self.output_dir / "postgres"
        self.minio_dir = self.output_dir / "minio"
        self.models_dir = self.output_dir / "models"
        self.configs_dir = self.output_dir / "configs"
        for d in (self.postgres_dir, self.minio_dir, self.models_dir, self.configs_dir):
            d.mkdir(parents=True, exist_ok=True)

    def backup_postgres(self, db_name: str = "neurobet", timestamp: Optional[str] = None) -> Dict[str, Any]:
        """
        Dumps PostgreSQL (TimescaleDB) database into compressed custom format (.dump)
        and queries table statistics and hypertable metadata.
        """
        ts = timestamp or utc_timestamp_str()
        dump_path = self.postgres_dir / f"neurobet_pg_{db_name}_{ts}.dump"
        meta_path = self.postgres_dir / f"neurobet_pg_{db_name}_{ts}.meta.json"

        print(f"[*] Starting PostgreSQL backup for database '{db_name}'...")

        # 1. Execute pg_dump using neurobet_postgres container
        dump_cmd = [
            "docker", "exec", "neurobet_postgres",
            "pg_dump", "-U", "neurobet", "-d", db_name,
            "-Fc", "--no-owner", "--no-acl"
        ]
        with open(dump_path, "wb") as f_out:
            run_cmd(dump_cmd, stdout=f_out)

        file_size = dump_path.stat().st_size
        sha256 = compute_sha256(dump_path)

        # 2. Extract database table count and key statistics
        stats_sql = """
        SELECT
            (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public') as table_count,
            (SELECT count(*) FROM events) as events_count,
            (SELECT count(*) FROM bets) as bets_count,
            (SELECT count(*) FROM ledger_entries) as ledger_count,
            (SELECT count(*) FROM event_state_snapshots) as event_snapshots_count,
            (SELECT count(*) FROM odds_snapshots) as odds_snapshots_count,
            (SELECT count(*) FROM feature_snapshots) as feature_snapshots_count,
            (SELECT pg_size_pretty(pg_database_size('""" + db_name + """'))) as db_size;
        """
        stats_res = run_cmd([
            "docker", "exec", "neurobet_postgres",
            "psql", "-U", "neurobet", "-d", db_name, "-t", "-A", "-F", "|", "-c", stats_sql
        ])
        stats_line = stats_res.stdout.decode("utf-8").strip()
        stats_parts = stats_line.split("|") if stats_line else []

        stats = {
            "tables": int(stats_parts[0]) if len(stats_parts) > 0 and stats_parts[0].isdigit() else 0,
            "events": int(stats_parts[1]) if len(stats_parts) > 1 and stats_parts[1].isdigit() else 0,
            "bets": int(stats_parts[2]) if len(stats_parts) > 2 and stats_parts[2].isdigit() else 0,
            "ledger_entries": int(stats_parts[3]) if len(stats_parts) > 3 and stats_parts[3].isdigit() else 0,
            "event_state_snapshots": int(stats_parts[4]) if len(stats_parts) > 4 and stats_parts[4].isdigit() else 0,
            "odds_snapshots": int(stats_parts[5]) if len(stats_parts) > 5 and stats_parts[5].isdigit() else 0,
            "feature_snapshots": int(stats_parts[6]) if len(stats_parts) > 6 and stats_parts[6].isdigit() else 0,
            "db_size": stats_parts[7] if len(stats_parts) > 7 else "unknown",
        }

        # 3. Timescale hypertables list
        ht_res = run_cmd([
            "docker", "exec", "neurobet_postgres",
            "psql", "-U", "neurobet", "-d", db_name, "-t", "-A", "-c",
            "SELECT hypertable_name FROM timescaledb_information.hypertables ORDER BY 1;"
        ])
        hypertables = [h.strip() for h in ht_res.stdout.decode("utf-8").strip().splitlines() if h.strip()]

        manifest = {
            "type": "postgres_backup",
            "database": db_name,
            "timestamp": ts,
            "dump_file": dump_path.name,
            "file_size_bytes": file_size,
            "sha256": sha256,
            "hypertables": hypertables,
            "stats": stats,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        with open(meta_path, "w", encoding="utf-8") as f_meta:
            json.dump(manifest, f_meta, indent=2)

        print(f"[+] PostgreSQL backup complete: {dump_path.name} ({file_size / (1024*1024):.2f} MB, SHA256: {sha256[:12]}...)")
        return manifest

    def backup_minio(self, timestamp: Optional[str] = None) -> Dict[str, Any]:
        """
        Creates compressed archive of MinIO storage data volume and manifests.
        """
        ts = timestamp or utc_timestamp_str()
        tar_path = self.minio_dir / f"minio_data_{ts}.tar.gz"
        meta_path = self.minio_dir / f"minio_data_{ts}.meta.json"

        print(f"[*] Starting MinIO backup from 'neurobet_minio' container...")

        # Execute tar on /data inside container and stream to host tar_path
        tar_cmd = ["docker", "exec", "neurobet_minio", "tar", "-czf", "-", "-C", "/data", "."]
        with open(tar_path, "wb") as f_out:
            run_cmd(tar_cmd, stdout=f_out)

        file_size = tar_path.stat().st_size
        sha256 = compute_sha256(tar_path)

        manifest = {
            "type": "minio_backup",
            "timestamp": ts,
            "archive_file": tar_path.name,
            "file_size_bytes": file_size,
            "sha256": sha256,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        with open(meta_path, "w", encoding="utf-8") as f_meta:
            json.dump(manifest, f_meta, indent=2)

        print(f"[+] MinIO backup complete: {tar_path.name} ({file_size / (1024*1024):.2f} MB, SHA256: {sha256[:12]}...)")
        return manifest

    def export_models(self, timestamp: Optional[str] = None) -> Dict[str, Any]:
        """
        Exports all trained models, checkpoints, synthetic datasets, and metadata into a tar.gz bundle.
        """
        ts = timestamp or utc_timestamp_str()
        tar_path = self.models_dir / f"models_export_{ts}.tar.gz"
        meta_path = self.models_dir / f"models_export_{ts}.meta.json"

        models_checkpoints_dir = WORKSPACE_ROOT / "models" / "checkpoints"
        print(f"[*] Exporting model artifacts from {models_checkpoints_dir}...")

        file_items = []
        with tarfile.open(tar_path, "w:gz") as tar:
            if models_checkpoints_dir.exists():
                for item in models_checkpoints_dir.iterdir():
                    if item.is_file() and item.name != ".gitkeep":
                        tar.add(item, arcname=item.name)
                        file_items.append({
                            "name": item.name,
                            "size_bytes": item.stat().st_size,
                            "sha256": compute_sha256(item),
                        })

        file_size = tar_path.stat().st_size
        sha256 = compute_sha256(tar_path)

        manifest = {
            "type": "models_export",
            "timestamp": ts,
            "archive_file": tar_path.name,
            "file_size_bytes": file_size,
            "sha256": sha256,
            "artifact_count": len(file_items),
            "artifacts": file_items,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        with open(meta_path, "w", encoding="utf-8") as f_meta:
            json.dump(manifest, f_meta, indent=2)

        print(f"[+] Model export complete: {tar_path.name} ({len(file_items)} artifacts, {file_size / 1024:.1f} KB)")
        return manifest

    def export_config(self, timestamp: Optional[str] = None) -> Dict[str, Any]:
        """
        Exports environment configs, docker-compose configuration, Prometheus rules,
        Alembic migration configurations, and nginx configs into a versioned bundle.
        """
        ts = timestamp or utc_timestamp_str()
        tar_path = self.configs_dir / f"config_export_{ts}.tar.gz"
        meta_path = self.configs_dir / f"config_export_{ts}.meta.json"

        print(f"[*] Exporting configuration bundle...")

        config_files = [
            ".env",
            "docker-compose.yml",
            "alembic.ini",
            "Makefile",
            "infra/postgres/init-timescale.sh",
            "infra/prometheus/prometheus.yml",
        ]

        file_items = []
        with tarfile.open(tar_path, "w:gz") as tar:
            for rel in config_files:
                p = WORKSPACE_ROOT / rel
                if p.exists() and p.is_file():
                    tar.add(p, arcname=rel)
                    file_items.append({
                        "path": rel,
                        "size_bytes": p.stat().st_size,
                        "sha256": compute_sha256(p),
                    })

        file_size = tar_path.stat().st_size
        sha256 = compute_sha256(tar_path)

        manifest = {
            "type": "config_export",
            "timestamp": ts,
            "archive_file": tar_path.name,
            "file_size_bytes": file_size,
            "sha256": sha256,
            "config_files_count": len(file_items),
            "files": file_items,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        with open(meta_path, "w", encoding="utf-8") as f_meta:
            json.dump(manifest, f_meta, indent=2)

        print(f"[+] Config export complete: {tar_path.name} ({len(file_items)} files, {file_size / 1024:.1f} KB)")
        return manifest

    def backup_all(self, db_name: str = "neurobet") -> Dict[str, Any]:
        """
        Creates full synchronized Disaster Recovery bundle for all 4 subsystems.
        """
        ts = utc_timestamp_str()
        print(f"============================================================")
        print(f"🚀 INITIATING FULL NEUROBET DISASTER RECOVERY BUNDLE [{ts}]")
        print(f"============================================================")

        pg_manifest = self.backup_postgres(db_name=db_name, timestamp=ts)
        minio_manifest = self.backup_minio(timestamp=ts)
        models_manifest = self.export_models(timestamp=ts)
        config_manifest = self.export_config(timestamp=ts)

        bundle_manifest = {
            "type": "dr_full_bundle",
            "bundle_timestamp": ts,
            "postgres": pg_manifest,
            "minio": minio_manifest,
            "models": models_manifest,
            "config": config_manifest,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        bundle_manifest_path = self.output_dir / f"dr_manifest_{ts}.json"
        with open(bundle_manifest_path, "w", encoding="utf-8") as f_b:
            json.dump(bundle_manifest, f_b, indent=2)

        print(f"============================================================")
        print(f"✅ FULL DR BUNDLE COMPLETED: {bundle_manifest_path.name}")
        print(f"============================================================")
        return bundle_manifest


def main():
    parser = argparse.ArgumentParser(description="Neurobet Disaster Recovery Backup Manager")
    parser.add_argument("--all", action="store_true", help="Execute full DR backup for all subsystems")
    parser.add_argument("--postgres", action="store_true", help="Backup PostgreSQL database")
    parser.add_argument("--minio", action="store_true", help="Backup MinIO storage")
    parser.add_argument("--models", action="store_true", help="Export model checkpoints & metadata")
    parser.add_argument("--config", action="store_true", help="Export system & app configs")
    parser.add_argument("--db-name", default="neurobet", help="PostgreSQL database name (default: neurobet)")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: /srv/neurobet/backups)")

    args = parser.parse_args()
    out = Path(args.output_dir) if args.output_dir else DEFAULT_BACKUP_DIR
    manager = BackupManager(output_dir=out)

    if args.all or not any([args.postgres, args.minio, args.models, args.config]):
        manager.backup_all(db_name=args.db_name)
    else:
        if args.postgres:
            manager.backup_postgres(db_name=args.db_name)
        if args.minio:
            manager.backup_minio()
        if args.models:
            manager.export_models()
        if args.config:
            manager.export_config()


if __name__ == "__main__":
    main()
