"""Read-only resource and cross-container Chromium-root sampler."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import subprocess
import time


SERVICES = ("research", "flashscore-worker")
ROOT_COUNT = (
    "import glob; "
    "print(sum(1 for p in glob.glob('/proc/[0-9]*/cmdline') "
    "if (lambda b: b'chrome-headless-shell-linux64/chrome-headless-shell' in b "
    "and b'--type=' not in b)(open(p,'rb').read())))"
)


def sample() -> dict:
    row: dict = {"observed_at": datetime.now(timezone.utc).isoformat(), "services": {}}
    for service in SERVICES:
        resolved = subprocess.run(
            ["docker", "ps", "--filter", "label=com.docker.compose.project=neurobet",
             "--filter", f"label=com.docker.compose.service={service}",
             "--filter", "status=running", "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=15, check=True,
        )
        container_ids = [value.strip() for value in resolved.stdout.splitlines() if value.strip()]
        if len(container_ids) != 1:
            raise RuntimeError(f"expected one running container for {service}, got {len(container_ids)}")
        container = container_ids[0]
        stats = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", container],
            capture_output=True, text=True, timeout=15, check=True,
        )
        container_stats = json.loads(stats.stdout)
        roots = subprocess.run(
            ["docker", "exec", container, "python", "-c", ROOT_COUNT],
            capture_output=True, text=True, timeout=15, check=True,
        )
        container_stats["browser_roots"] = int(roots.stdout.strip())
        row["services"][service] = container_stats
    row["total_browser_roots"] = sum(item["browser_roots"] for item in row["services"].values())
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=int, default=1800)
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    deadline = time.monotonic() + max(1, args.duration_seconds)
    with open(args.output, "a", encoding="utf-8") as handle:
        while True:
            try:
                row = sample()
            except Exception as exc:
                row = {"observed_at": datetime.now(timezone.utc).isoformat(),
                       "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(max(1, args.interval_seconds), remaining))


if __name__ == "__main__":
    main()
