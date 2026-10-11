#!/usr/bin/env python3
"""CLI tool to execute MinIO storage lifecycle policies, deduplication, and cleanup."""

import argparse
import json
import logging
import sys

from db.storage_lifecycle import StorageLifecycleManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("storage_lifecycle_cli")


def main():
    parser = argparse.ArgumentParser(description="Neurobet Storage Lifecycle & Archive Policy Tool")
    parser.add_argument("--action", choices=["metrics", "apply-policy", "cleanup"], default="metrics",
                        help="Action to perform: metrics, apply-policy, cleanup")
    parser.add_argument("--retention-days", type=int, default=14,
                        help="Retention period in days for raw snapshot payloads")
    parser.add_argument("--dry-run", action="store_true",
                        help="Execute cleanup in dry-run mode without deleting objects")

    args = parser.parse_args()
    manager = StorageLifecycleManager()

    if args.action == "metrics":
        metrics = manager.measure_storage_metrics()
        print("\n=== Neurobet Raw Storage Metrics ===")
        print(json.dumps(metrics, indent=2, ensure_ascii=False))

    elif args.action == "apply-policy":
        res = manager.apply_lifecycle_configuration(expiration_days=args.retention_days * 2)
        print("\n=== Lifecycle Configuration Result ===")
        print(json.dumps(res, indent=2, ensure_ascii=False))

    elif args.action == "cleanup":
        res = manager.execute_archive_cleanup(retention_days=args.retention_days, dry_run=args.dry_run)
        print("\n=== Raw Storage Archive Cleanup Result ===")
        print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
