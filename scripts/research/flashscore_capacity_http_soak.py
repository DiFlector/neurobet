"""Capture 15-second Flashscore queue and host/container health samples."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import subprocess
import time
from urllib.request import Request, urlopen


CANONICAL_CONTAINERS = {
    "research": "neurobet_research",
    "flashscore-worker": "neurobet_flashscore_worker",
    "external-tennis-sources": "neurobet_external_tennis_sources",
    "backend": "neurobet_backend",
}


def command(args: list[str]) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=15).stdout.strip()


def container_id(service: str) -> str:
    expected_name = CANONICAL_CONTAINERS[service]
    rows = command([
        "docker", "ps", "--filter", "label=com.docker.compose.project=neurobet",
        "--filter", f"label=com.docker.compose.service={service}",
        "--filter", "label=com.docker.compose.oneoff=False",
        "--filter", "status=running", "--format", "{{.ID}} {{.Names}}",
    ]).splitlines()
    canonical = [row.split(maxsplit=1)[0] for row in rows
                 if len(row.split(maxsplit=1)) == 2 and row.split(maxsplit=1)[1] == expected_name]
    if len(canonical) != 1:
        raise RuntimeError(
            f"expected one running canonical container {expected_name} for {service}, got {len(canonical)}"
        )
    return canonical[0]


def cgroup_counters(container: str, path: str) -> dict[str, int] | None:
    try:
        raw = command(["docker", "exec", container, "cat", path])
        return {key: int(value) for key, value in
                (line.split() for line in raw.splitlines() if len(line.split()) == 2)}
    except (subprocess.CalledProcessError, RuntimeError, ValueError):
        return None


def get_json(url: str, timeout: float = 10) -> dict:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def sample(queue_url: str, source_status_url: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    row: dict = {"observed_at": now, "services": {}}
    queue_started = time.monotonic()
    payload = get_json(queue_url)
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
    live_waits = sorted(
        int(item["wait_seconds"]) for item in items
        if item.get("kind") == "pbp" and item.get("mode") == "live"
        and item.get("wait_seconds") is not None
    )
    row["queue"]["live_pbp_wait_seconds"] = {
        "count": len(live_waits), "p95": live_waits[min(len(live_waits) - 1, int((len(live_waits) - 1) * .95))]
        if live_waits else None,
        "max": live_waits[-1] if live_waits else None,
    }
    row["queue"]["source_control"] = {
        key: (payload.get("worker") or {}).get(key)
        for key in ("network_slots", "network_slots_limit", "cooldown_until", "healthy", "browser_lanes", "player_history_slots")
    }

    source_started = time.monotonic()
    sources = get_json(source_status_url)
    row["source_status_seconds"] = round(time.monotonic() - source_started, 3)
    row["source_status"] = {
        source.get("source"): {
            "status": source.get("status"), "quota": source.get("quota"),
            "jobs": source.get("jobs"), "point_documents": source.get("point_documents"),
            "profile_history": source.get("profile_history"),
        }
        for source in sources.get("sources", [])
    }

    meminfo = {}
    with open("/proc/meminfo", encoding="ascii") as handle:
        for line in handle:
            key, value = line.split(":", 1)
            meminfo[key] = int(value.strip().split()[0]) * 1024
    row["host_memory"] = {key: meminfo.get(key) for key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")}

    for service in ("research", "flashscore-worker", "external-tennis-sources", "backend"):
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
            "cpu_throttling": cgroup_counters(cid, "/sys/fs/cgroup/cpu.stat"),
            "memory_events": cgroup_counters(cid, "/sys/fs/cgroup/memory.events"),
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
    parser.add_argument("--source-status-url", default="http://127.0.0.1:8005/api/tennis-history/sources/status")
    parser.add_argument("--duration-seconds", type=int, default=1800)
    parser.add_argument("--interval-seconds", type=int, default=15)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    deadline = time.monotonic() + max(1, args.duration_seconds)
    next_sample_at = time.monotonic()
    with open(args.output, "a", encoding="utf-8") as output:
        while True:
            try:
                row = sample(args.queue_url, args.source_status_url)
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
