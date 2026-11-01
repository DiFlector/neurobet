"""Observe the full new-event enrollment cohort without changing jobs."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def sample(base_url: str) -> dict:
    observed = datetime.now(timezone.utc).isoformat()
    items, offset, total, summary, pages = [], 0, None, None, 0
    try:
        while total is None or offset < total:
            with urlopen(f"{base_url}/api/tennis-history/enrollment?hours=25&limit=200&offset={offset}", timeout=45) as response:
                payload = json.load(response)
            if total is None:
                total, summary = payload["total"], payload["summary"]
            page = payload["items"]
            items.extend(page)
            pages += 1
            if not page:
                break
            offset += len(page)
        unique = {item["event_id"] for item in items}
        return {"observed_at": observed, "status": "complete" if len(unique) == total else "incomplete",
                "total": total, "observed_events": len(unique), "pages": pages,
                "summary": summary, "items": items}
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
        return {"observed_at": observed, "status": "error", "error": type(exc).__name__,
                "pages": pages, "observed_events": len(items), "total": total}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8005")
    parser.add_argument("--duration-seconds", type=int, default=86400)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    started = time.monotonic()
    started_at = datetime.now(timezone.utc)
    events = {}
    samples = Counter()
    with open(args.output, "a", encoding="utf-8") as output:
        while True:
            row = sample(args.base_url)
            samples[row["status"]] += 1
            for item in row.get("items", []):
                first_seen = datetime.fromisoformat(item["first_seen_at"].replace("Z", "+00:00"))
                if first_seen >= started_at:
                    events[item["event_id"]] = item
            row["elapsed_seconds"] = round(time.monotonic() - started, 2)
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
            output.flush()
            remaining = args.duration_seconds - (time.monotonic() - started)
            if remaining <= 0:
                break
            time.sleep(min(max(args.interval_seconds, 1), remaining))
    reasons = Counter((item["history_job"]["reason"] or item["lookup"]["reason"] or "none")
                      for item in events.values())
    summary = {"started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 2), "samples": dict(samples),
        "status": "complete" if row["status"] == "complete" and not samples["error"] and not samples["incomplete"] else "incomplete",
        "new_events_observed": len(events), "registered": sum(bool(x["history_job"]["status"]) for x in events.values()),
        "linked": sum(bool(x["links"]) for x in events.values()),
        "with_documents": sum(bool(x["documents"]) for x in events.values()),
        "with_parsed_history": sum(bool(x["parsed_history"]) for x in events.values()),
        "reasons": dict(reasons), "target_eligibility": "requires_label_audit"}
    with open(args.output + ".summary.json", "w", encoding="utf-8") as output:
        json.dump(summary, output, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
