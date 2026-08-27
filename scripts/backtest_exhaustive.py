#!/usr/bin/env python3
"""Exhaustive sports×markets grid (all non-empty subsets). Resumes + restores settings."""
from __future__ import annotations

import itertools
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://diflector.ru/neurobet/api/admin"
LIMIT = 80000
TIMEOUT = 600
OUT = Path("/tmp/neurobet_exhaustive.json")
PROGRESS = Path("/tmp/neurobet_exhaustive_progress.json")

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
    out: list[list[str]] = []
    for r in range(1, len(items) + 1):
        for combo in itertools.combinations(items, r):
            out.append(sorted(combo))
    return out


def combo_key(sports: list[str], markets: list[str]) -> str:
    return f"S={'|'.join(sports)};M={'|'.join(markets)}"


def get_settings() -> dict:
    return _req("GET", "/ai-settings")["settings"]


def set_settings(enabled_sports: list[str], enabled_markets: list[str]) -> None:
    _req("POST", "/ai-settings", {
        "enabled_sports": enabled_sports,
        "enabled_markets": enabled_markets,
    })


def run_backtest() -> dict:
    return _req("POST", "/backtest", {"limit": LIMIT})


def get_review() -> dict:
    return _req("GET", "/backtest/review")


def extract(review: dict) -> dict:
    r = review.get("review") or {}
    summary = r.get("summary") or {}
    wf = (r.get("slices") or {}).get("walk_forward") or {}
    core = (
        (wf.get("bets") or 0) >= 40
        and (wf.get("roi_pct") or 0) > 0
        and (wf.get("roi_pct_lo") or -999) > 0
        and wf.get("brier_beats_market") is True
    )
    return {
        "edge_verdict": summary.get("edge_verdict"),
        "quality_gate_pass": summary.get("quality_gate_pass"),
        "core_pass": core,
        "wf_roi": wf.get("roi_pct"),
        "wf_roi_lo": wf.get("roi_pct_lo"),
        "wf_roi_hi": wf.get("roi_pct_hi"),
        "wf_bets": wf.get("bets"),
        "wf_brier": wf.get("brier"),
        "wf_market_brier": wf.get("market_brier"),
        "brier_beats_market": wf.get("brier_beats_market"),
        "overall_roi_lo": ((r.get("slices") or {}).get("overall") or {}).get("roi_pct_lo"),
        "one_liner": summary.get("one_liner"),
    }


def save_state(original: dict, results: list[dict], done_keys: set[str]) -> None:
    payload = {"original": original, "done_keys": sorted(done_keys), "results": results}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    PROGRESS.write_text(
        json.dumps({"total": 961, "done": len(done_keys), "ts": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )


def rank(results: list[dict]) -> list[dict]:
    return sorted(
        results,
        key=lambda x: (
            x.get("core_pass") or False,
            x.get("quality_gate_pass") or False,
            x.get("wf_roi_lo") if x.get("wf_roi_lo") is not None else -9999,
            x.get("wf_roi") if x.get("wf_roi") is not None else -9999,
            x.get("wf_bets") or 0,
        ),
        reverse=True,
    )


def main() -> int:
    sport_sets = subsets(ALL_SPORTS)
    market_sets = subsets(ALL_MARKETS)
    all_combos = [(s, m) for s in sport_sets for m in market_sets]
    assert len(all_combos) == 961

    done_keys: set[str] = set()
    results: list[dict] = []
    original: dict | None = None

    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            original = prev.get("original")
            results = prev.get("results") or []
            done_keys = set(prev.get("done_keys") or [])
            print(f"Resuming: {len(done_keys)}/{len(all_combos)} done", flush=True)
        except Exception:
            pass

    if original is None:
        print("Saving original settings…", flush=True)
        original = get_settings()
        print(f"  {original['enabled_sports']} / {original['enabled_markets']}", flush=True)

    t_all = time.time()
    try:
        for i, (sports, markets) in enumerate(all_combos, 1):
            key = combo_key(sports, markets)
            if key in done_keys:
                continue
            print(f"[{len(done_keys)+1}/{len(all_combos)}] {key}", flush=True)
            t0 = time.time()
            try:
                set_settings(sports, markets)
                time.sleep(0.3)
                bt = run_backtest()
                review = get_review()
                metrics = extract(review)
                row = {
                    "key": key,
                    "sports": sports,
                    "markets": markets,
                    "samples": bt.get("samples_evaluated", 0),
                    "elapsed_s": round(time.time() - t0, 1),
                    **metrics,
                }
            except Exception as e:
                row = {
                    "key": key,
                    "sports": sports,
                    "markets": markets,
                    "error": str(e),
                    "elapsed_s": round(time.time() - t0, 1),
                }
            results.append(row)
            done_keys.add(key)
            if len(done_keys) % 5 == 0 or len(done_keys) == len(all_combos):
                save_state(original, results, done_keys)
            if "wf_roi_lo" in row:
                print(
                    f"  WF {row.get('wf_roi')}% lo={row.get('wf_roi_lo')}% "
                    f"bets={row.get('wf_bets')} core={row.get('core_pass')}",
                    flush=True,
                )
            else:
                print(f"  ERROR: {row.get('error')}", flush=True)
    finally:
        if original:
            print("Restoring settings…", flush=True)
            set_settings(original["enabled_sports"], original["enabled_markets"])
        save_state(original or {}, results, done_keys)

    elapsed = round(time.time() - t_all, 1)
    ranked = rank([r for r in results if "wf_roi_lo" in r])
    summary = {
        "elapsed_total_s": elapsed,
        "combinations": len(all_combos),
        "completed": len(done_keys),
        "core_pass_count": sum(1 for r in results if r.get("core_pass")),
        "top10": ranked[:10],
        "best": ranked[0] if ranked else None,
    }
    Path("/tmp/neurobet_exhaustive_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"\nDone in {elapsed}s. Best:", flush=True)
    if ranked:
        b = ranked[0]
        print(
            f"  sports={b['sports']} markets={b['markets']} "
            f"WF lo={b['wf_roi_lo']}% roi={b['wf_roi']}% bets={b['wf_bets']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as e:
        print(e.read().decode(), file=sys.stderr)
        raise SystemExit(1)
