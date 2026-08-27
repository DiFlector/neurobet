#!/usr/bin/env python3
"""Grid search sports×markets via prod admin API. Restores settings on exit."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "https://diflector.ru/neurobet/api/admin"
LIMIT = 80000
TIMEOUT = 600


def _req(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


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


COMBOS: list[tuple[str, list[str], list[str]]] = [
    ("fb_over", ["футбол"], ["total_over"]),
    ("fb_outcomes", ["футбол"], ["w1", "w2", "draw"]),
    ("fb_over_outcomes", ["футбол"], ["total_over", "w1", "w2", "draw"]),
    ("fb_totals", ["футбол"], ["total_over", "total_under"]),
    ("fb_all_mkts", ["футбол"], ["draw", "total_over", "total_under", "w1", "w2"]),
    ("fb_under", ["футбол"], ["total_under"]),
    ("tt_over", ["настольный теннис"], ["total_over"]),
    ("tt_totals", ["настольный теннис"], ["total_over", "total_under"]),
    ("fb_vb_cur", ["футбол", "волейбол"], ["draw", "total_over", "w1", "w2"]),
    ("multi_over", ["настольный теннис", "теннис", "баскетбол", "футбол"], ["total_over"]),
    ("fb_vb_totals", ["футбол", "волейбол"], ["total_over", "total_under"]),
    ("all_outcomes", ["настольный теннис", "теннис", "баскетбол", "футбол", "волейбол"], ["w1", "w2", "draw"]),
]


def extract_metrics(review: dict) -> dict:
    r = review.get("review") or {}
    summary = r.get("summary") or {}
    wf = (r.get("slices") or {}).get("walk_forward") or {}
    qg = r.get("quality_gate") or {}
    core_pass = (
        (wf.get("bets") or 0) >= 40
        and (wf.get("roi_pct") or 0) > 0
        and (wf.get("roi_pct_lo") or -999) > 0
        and wf.get("brier_beats_market") is True
    )
    return {
        "edge_verdict": summary.get("edge_verdict"),
        "quality_gate_pass": summary.get("quality_gate_pass"),
        "core_metrics_pass": core_pass,
        "wf_roi": wf.get("roi_pct"),
        "wf_roi_lo": wf.get("roi_pct_lo"),
        "wf_roi_hi": wf.get("roi_pct_hi"),
        "wf_bets": wf.get("bets"),
        "wf_brier": wf.get("brier"),
        "wf_market_brier": wf.get("market_brier"),
        "brier_beats_market": wf.get("brier_beats_market"),
        "gate_reasons": qg.get("reasons") or [],
        "one_liner": summary.get("one_liner"),
        "by_market": r.get("by_market"),
        "by_sport": r.get("by_sport"),
    }


def main() -> int:
    print("Saving original settings…", flush=True)
    original = get_settings()
    orig_sports = original["enabled_sports"]
    orig_markets = original["enabled_markets"]
    print(f"  sports={orig_sports}, markets={orig_markets}", flush=True)

    results: list[dict] = []
    try:
        for i, (label, sports, markets) in enumerate(COMBOS, 1):
            print(f"\n[{i}/{len(COMBOS)}] {label}: sports={sports}, markets={markets}", flush=True)
            t0 = time.time()
            set_settings(sports, markets)
            time.sleep(0.5)
            bt = run_backtest()
            status = bt.get("status", "ok")
            samples = bt.get("samples_evaluated", 0)
            review = get_review()
            metrics = extract_metrics(review)
            elapsed = round(time.time() - t0, 1)
            row = {
                "label": label,
                "sports": sports,
                "markets": markets,
                "backtest_status": status,
                "samples_evaluated": samples,
                "elapsed_s": elapsed,
                **metrics,
            }
            results.append(row)
            print(
                f"  → WF ROI {metrics['wf_roi']}% (CI lo {metrics['wf_roi_lo']}%), "
                f"bets={metrics['wf_bets']}, gate={metrics['quality_gate_pass']}, "
                f"verdict={metrics['edge_verdict']}, {elapsed}s",
                flush=True,
            )
    finally:
        print("\nRestoring original settings…", flush=True)
        set_settings(orig_sports, orig_markets)
        print(f"  restored sports={orig_sports}, markets={orig_markets}", flush=True)

    out_path = "/tmp/neurobet_grid_search.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"original": original, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to {out_path}", flush=True)

    ranked = sorted(
        results,
        key=lambda x: (
            x.get("core_metrics_pass") or False,
            x.get("wf_roi_lo") if x.get("wf_roi_lo") is not None else -999,
            x.get("wf_roi") if x.get("wf_roi") is not None else -999,
        ),
        reverse=True,
    )
    print("\n=== TOP 5 (by core pass + WF CI lo) ===", flush=True)
    for r in ranked[:5]:
        print(
            f"  {r['label']}: sports={r['sports']}, markets={r['markets']} | "
            f"WF ROI {r['wf_roi']}% CI [{r['wf_roi_lo']}, {r['wf_roi_hi']}] | "
            f"gate={r['quality_gate_pass']} core={r['core_metrics_pass']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as e:
        print(f"HTTP error: {e.code} {e.read().decode()}", file=sys.stderr)
        raise SystemExit(1)
