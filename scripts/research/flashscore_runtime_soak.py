"""Read-only runtime soak for the Flashscore queue API.

Example:
  python scripts/research/flashscore_runtime_soak.py \
    --duration-seconds 1800 --interval-seconds 30 \
    --output docs/research/flashscore_soak_2026-10-21_post-authoritative-versions.jsonl
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def sample(url: str, timeout: float) -> dict:
    started = time.monotonic()
    observed_at = datetime.now(timezone.utc).isoformat()
    row = {"observed_at": observed_at}
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
            row.update({
                "http_status": response.status,
                "latency_seconds": round(time.monotonic() - started, 3),
                "eligible_matches": payload.get("eligible_matches"),
                "prepared_players": payload.get("prepared_players"),
                "statuses": payload.get("statuses"),
                "identity_confirmation_required": payload.get("identity_confirmation_required"),
                "overdue_pbp": payload.get("overdue_pbp"),
                "documents_saved_last_hour": payload.get("documents_saved_last_hour"),
                "confirmed_links_last_hour": payload.get("confirmed_links_last_hour"),
                "worker": payload.get("worker"),
                "job_status_counts": {
                    status: sum(1 for item in payload.get("items", []) if item.get("status") == status)
                    for status in sorted({item.get("status") for item in payload.get("items", []) if item.get("status")})
                },
                "oldest_job_wait_seconds": max(
                    (int(item.get("wait_seconds") or 0) for item in payload.get("items", [])),
                    default=0,
                ),
                "oldest_job_reason": next((
                    item.get("reason") for item in sorted(
                        payload.get("items", []), key=lambda item: int(item.get("wait_seconds") or 0), reverse=True
                    ) if item.get("wait_seconds") is not None
                ), None),
            })
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        row.update({"http_status": getattr(exc, "code", None),
                    "latency_seconds": round(time.monotonic() - started, 3),
                    "error": type(exc).__name__})
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8005/api/tennis-history/flashscore/queue?limit=1&offset=0")
    parser.add_argument("--duration-seconds", type=int, default=1800)
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=float, default=10)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    deadline = time.monotonic() + max(1, args.duration_seconds)
    with open(args.output, "a", encoding="utf-8") as output:
        while True:
            row = sample(args.url, args.timeout_seconds)
            output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            output.flush()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(max(1, args.interval_seconds), remaining))


if __name__ == "__main__":
    main()
