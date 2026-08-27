#!/usr/bin/env python3
"""Official API exhaustive grid — 961 combos (all non-empty sport×market subsets)."""
from __future__ import annotations

import itertools
import json
import time
import urllib.request
from pathlib import Path

BASE = "https://diflector.ru/neurobet/api/admin"
LIMIT = 30000
TIMEOUT = 600
OUT = Path("/tmp/neurobet_api_exhaustive.json")

SPORTS = ["баскетбол", "волейбол", "настольный теннис", "теннис", "футбол"]
MARKETS = ["draw", "total_over", "total_under", "w1", "w2"]


def req(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(r, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def subsets(items):
    for n in range(1, len(items) + 1):
        for c in itertools.combinations(items, n):
            yield sorted(c)


def metrics(review):
    r = review.get("review") or {}
    wf = (r.get("slices") or {}).get("walk_forward") or {}
    core = (
        (wf.get("bets") or 0) >= 40
        and (wf.get("roi_pct") or -999) > 0
        and (wf.get("roi_pct_lo") or -999) > 0
        and wf.get("brier_beats_market")
    )
    return {
        "wf_roi": wf.get("roi_pct"),
        "wf_roi_lo": wf.get("roi_pct_lo"),
        "wf_bets": wf.get("bets"),
        "core_pass": core,
        "edge": (r.get("summary") or {}).get("edge_verdict"),
        "one_liner": (r.get("summary") or {}).get("one_liner"),
    }


def rank(rows):
    return sorted(
        rows,
        key=lambda x: (
            x.get("core_pass") or False,
            x.get("wf_roi_lo") if x.get("wf_roi_lo") is not None else -9999,
            x.get("wf_roi") if x.get("wf_roi") is not None else -9999,
        ),
        reverse=True,
    )


def main():
    combos = [(list(s), list(m)) for s in subsets(SPORTS) for m in subsets(MARKETS)]
    orig = req("GET", "/ai-settings")["settings"]
    done = set()
    results = []
    if OUT.exists():
        prev = json.loads(OUT.read_text())
        results = prev.get("results") or []
        done = {json.dumps([r["sports"], r["markets"]], ensure_ascii=False) for r in results}

    try:
        for sports, markets in combos:
            k = json.dumps([sports, markets], ensure_ascii=False)
            if k in done:
                continue
            t0 = time.time()
            req("POST", "/ai-settings", {"enabled_sports": sports, "enabled_markets": markets})
            time.sleep(0.2)
            req("POST", "/backtest", {"limit": LIMIT})
            m = metrics(req("GET", "/backtest/review"))
            row = {"sports": sports, "markets": markets, "elapsed": round(time.time() - t0, 1), **m}
            results.append(row)
            done.add(k)
            if len(results) % 10 == 0:
                OUT.write_text(json.dumps({"original": orig, "results": results}, ensure_ascii=False, indent=2))
                top = rank(results)[0]
                print(f"[{len(results)}/{len(combos)}] best lo={top.get('wf_roi_lo')} {top['sports']}×{top['markets']}", flush=True)
    finally:
        req("POST", "/ai-settings", {"enabled_sports": orig["enabled_sports"], "enabled_markets": orig["enabled_markets"]})

    ranked = rank(results)
    payload = {"original": orig, "limit": LIMIT, "results": results, "best": ranked[0] if ranked else None, "top20": ranked[:20]}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print("BEST:", ranked[0] if ranked else None, flush=True)


if __name__ == "__main__":
    main()
