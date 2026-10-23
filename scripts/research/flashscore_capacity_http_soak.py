"""Capture 15-second Flashscore queue and host/container health samples."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import subprocess
import time
from urllib.request import urlopen


def command(args: list[str]) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=15).stdout.strip()


def container_id(service: str) -> str:
    ids = command([
        "docker", "ps", "--filter", "label=com.docker.compose.project=neurobet",
        "--filter", f"label=com.docker.compose.service={service}",
        "--filter", "status=running", "--format", "{{.ID}}",
    ]).splitlines()
    if len(ids) != 1:
        raise RuntimeError(f"expected one running {service}, got {len(ids)}")
    return ids[0]


def sample(queue_url: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    row: dict = {"observed_at": now, "services": {}}
    queue_started = time.monotonic()
    with urlopen(queue_url, timeout=10) as response:
        payload = json.loads(response.read())
    row["queue_api_seconds"] = round(time.monotonic() - queue_started, 3)
    items = payload.get("items", [])
    row["worker"] = payload.get("worker")
    row["queue"] = {
        "items": [{k: item.get(k) for k in (
            "id", "kind", "status", "mode", "priority", "attempts", "attempt_seconds", "wait_seconds",
            "retry_in_seconds", "last_success_at", "reason", "related_events",
        )} for item in items],
        "counts_by_kind_status": {},
    }
    for item in items:
        key = f"{item.get('kind')}:{item.get('status')}"
        row["queue"]["counts_by_kind_status"][key] = row["queue"]["counts_by_kind_status"].get(key, 0) + 1

    meminfo = {}
    with open("/proc/meminfo", encoding="ascii") as handle:
        for line in handle:
            key, value = line.split(":", 1)
            meminfo[key] = int(value.strip().split()[0]) * 1024
    row["host_memory"] = {key: meminfo.get(key) for key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")}

    for service in ("research", "flashscore-worker", "backend"):
        cid = container_id(service)
        stats = json.loads(command(["docker", "stats", "--no-stream", "--format", "{{json .}}", cid]))
        inspect = json.loads(command(["docker", "inspect", cid]))[0]
        item = inspect["State"]
        detail = {
            "container_id": cid,
            "health": (item.get("Health") or {}).get("Status", item.get("Status")),
            "restart_count": inspect.get("RestartCount"),
            "oom_killed": item.get("OOMKilled"),
            "stats": stats,
        }
        if service in ("research", "flashscore-worker"):
            roots = command([
                "docker", "exec", cid, "python", "-c",
                "import glob; print(sum(1 for p in glob.glob('/proc/[0-9]*/cmdline') if (lambda b: b'chrome-headless-shell-linux64/chrome-headless-shell' in b and b'--type=' not in b)(open(p,'rb').read())))",
            ])
            detail["browser_roots"] = int(roots)
        row["services"][service] = detail
    row["browser_roots_total"] = sum(
        detail.get("browser_roots", 0) for detail in row["services"].values()
    )
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-url", default="http://127.0.0.1:8005/api/tennis-history/flashscore/queue?limit=200&offset=0&include_completed=true")
    parser.add_argument("--duration-seconds", type=int, default=1800)
    parser.add_argument("--interval-seconds", type=int, default=15)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    deadline = time.monotonic() + max(1, args.duration_seconds)
    next_sample_at = time.monotonic()
    with open(args.output, "a", encoding="utf-8") as output:
        while True:
            try:
                row = sample(args.queue_url)
            except Exception as exc:
                row = {"observed_at": datetime.now(timezone.utc).isoformat(),
                       "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
            output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            output.flush()
            next_sample_at += max(1, args.interval_seconds)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            delay = max(0, next_sample_at - time.monotonic())
            time.sleep(min(delay, remaining))


if __name__ == "__main__":
    main()
