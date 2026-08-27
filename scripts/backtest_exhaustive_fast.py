#!/usr/bin/env python3
"""Two-phase exhaustive search: screen all 961 @ 25k samples, full @ 80k for top candidates."""
from __future__ import annotations

import itertools
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://diflector.ru/neurobet/api/admin"
SCREEN_LIMIT = 25000
FULL_LIMIT = 80000
TIMEOUT = 600
OUT = Path("/tmp/neurobet_exhaustive_full.json")

ALL_SPORTS = ["баскетбол", "волейбол", "настольный теннис", "теннис", "футбол"]
ALL_MARKETS = ["draw", "total_over", "total_under", "w1", "w2"]


def _req(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def subsets(items: list[str]) -> list[list[str]]:
    return [list(c) for r in range(1, len(items) + 1) for c in itertools.combinations(items, r)]


def key(sports: list[str], markets: list[str]) -> str:
    return f"S={'|'.join(sorted(sports))};M={'|'.join(sorted(markets))}"


def get_settings() -> dict:
    return _req("GET", "/ai-settings")["settings"]


def set_settings(sports: list[str], markets: list[str]) -> None:
    _req("POST", "/ai-settings", {"enabled_sports": sports, "enabled_markets": markets})


def run_bt(limit: int) -> dict:
    return _req("POST", "/backtest", {"limit": limit})


def metrics(review: dict) -> dict:
    r = review.get("review") or {}
    wf = (r.get("slices") or {}).get("walk_forward") or {}
    core = (
        (wf.get("bets") or 0) >= 40
        and (wf.get("roi_pct") or -999) > 0
        and (wf.get("roi_pct_lo") or -999) > 0
        and wf.get("brier_beats_market") is True
    )
    return {
        "edge_verdict": (r.get("summary") or {}).get("edge_verdict"),
        "core_pass": core,
        "wf_roi": wf.get("roi_pct"),
        "wf_roi_lo": wf.get("roi_pct_lo"),
        "wf_roi_hi": wf.get("roi_pct_hi"),
        "wf_bets": wf.get("bets"),
        "brier_beats_market": wf.get("brier_beats_market"),
        "one_liner": (r.get("summary") or {}).get("one_liner"),
    }


def rank(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda x: (
            x.get("core_pass") or False,
            x.get("wf_roi_lo") if x.get("wf_roi_lo") is not None else -9999,
            x.get("wf_roi") if x.get("wf_roi") is not None else -9999,
            x.get("wf_bets") or 0,
        ),
        reverse=True,
    )


def run_combo(sports: list[str], markets: list[str], limit: int, phase: str) -> dict:
    t0 = time.time()
    set_settings(sports, markets)
    time.sleep(0.2)
    bt = run_bt(limit)
    m = metrics(_req("GET", "/backtest/review"))
    return {
        "phase": phase,
        "limit": limit,
        "sports": sports,
        "markets": markets,
        "key": key(sports, markets),
        "samples": bt.get("samples_evaluated"),
        "elapsed_s": round(time.time() - t0, 1),
        **m,
    }


def main() -> int:
    combos = [(list(s), list(m)) for s in subsets(ALL_SPORTS) for m in subsets(ALL_MARKETS)]
    print(f"Phase 1: screen {len(combos)} combos @ limit={SCREEN_LIMIT}", flush=True)
    original = get_settings()
    screen: list[dict] = []
    t0 = time.time()
    try:
        for i, (sports, markets) in enumerate(combos, 1):
            try:
                row = run_combo(sports, markets, SCREEN_LIMIT, "screen")
            except Exception as e:
                row = {"key": key(sports, markets), "sports": sports, "markets": markets, "error": str(e)}
            screen.append(row)
            if i % 20 == 0 or i == len(combos):
                print(f"  [{i}/{len(combos)}] latest lo={row.get('wf_roi_lo')} core={row.get('core_pass')}", flush=True)
    finally:
        pass

    ranked = rank([r for r in screen if r.get("wf_roi_lo") is not None])
    core_pass = [r for r in ranked if r.get("core_pass")]
    # Full re-run: all core_pass + top 15 by roi_lo
    full_keys: set[str] = set()
    candidates: list[tuple[list[str], list[str]]] = []
    for r in core_pass + ranked[:15]:
        k = r["key"]
        if k not in full_keys:
            full_keys.add(k)
            candidates.append((r["sports"], r["markets"]))

    print(f"\nPhase 1 done in {round(time.time()-t0)}s. core_pass={len(core_pass)}. Phase 2: {len(candidates)} full runs", flush=True)
    full_rows: list[dict] = []
    try:
        for i, (sports, markets) in enumerate(candidates, 1):
            print(f"  full [{i}/{len(candidates)}] {key(sports, markets)}", flush=True)
            try:
                row = run_combo(sports, markets, FULL_LIMIT, "full")
            except Exception as e:
                row = {"key": key(sports, markets), "sports": sports, "markets": markets, "error": str(e), "phase": "full"}
            full_rows.append(row)
            print(f"    WF lo={row.get('wf_roi_lo')}% roi={row.get('wf_roi')}% bets={row.get('wf_bets')}", flush=True)
    finally:
        print("Restoring settings…", flush=True)
        set_settings(original["enabled_sports"], original["enabled_markets"])

    final_rank = rank(full_rows)
    out = {
        "original": original,
        "screen_total": len(combos),
        "screen_core_pass": len(core_pass),
        "full_rerun_count": len(candidates),
        "screen_top20": ranked[:20],
        "full_ranked": final_rank,
        "best": final_rank[0] if final_rank else None,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== BEST (full limit) ===", flush=True)
    if final_rank:
        b = final_rank[0]
        print(f"sports={b['sports']}", flush=True)
        print(f"markets={b['markets']}", flush=True)
        print(f"WF ROI={b['wf_roi']}% CI lo={b['wf_roi_lo']}% bets={b['wf_bets']} core={b['core_pass']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
