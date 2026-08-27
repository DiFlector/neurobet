#!/usr/bin/env python3
"""
In-process exhaustive sports×markets grid (961 combos) — one score pass, fast re-aggregate.
Run inside neurobet_ai container:
  python /app/scripts/backtest_grid_local.py
"""
from __future__ import annotations

import itertools
import json
import sys
import time
from typing import Any

from neurobet_filters import (
    ALLOWED_MARKET_FAMILIES,
    ALLOWED_SPORTS,
    set_enabled_markets,
    set_enabled_sports,
    sport_top,
)
from neurobet_features import (
    MARKET_FAMILIES,
    build_model_input,
    build_team_form_asof_lookup,
    build_team_stats_asof_lookup,
    market_family_index,
    row_to_sample,
)

from app.neuralbet.backtest import (
    BACKTEST_DEFAULT_LIMIT,
    _agg_group,
    _fetch_backtest_rows,
    _records_from_scored,
    _walk_forward_eval,
)
from app.neuralbet.calibration import get_calibration_buckets
from app.neuralbet.pipeline import (
    _engine_lock,
    _refresh_market_support,
    cycle_aborted,
    ensemble_engine,
)

ALL_SPORTS = sorted(ALLOWED_SPORTS)
ALL_MARKETS = sorted(ALLOWED_MARKET_FAMILIES)
LIMIT = int(__import__("os").getenv("NEURALBET_GRID_LIMIT", str(BACKTEST_DEFAULT_LIMIT)))


def subsets(items: list[str]) -> list[list[str]]:
    return [list(c) for r in range(1, len(items) + 1) for c in itertools.combinations(items, r)]


def market_label(factor_id: int) -> str:
    fam = market_family_index(factor_id)
    if fam < len(MARKET_FAMILIES):
        return MARKET_FAMILIES[fam]
    return "other"


def score_all(limit: int) -> tuple[list[dict], list[Any], dict, float, dict]:
    """Fetch all sports/markets, score once. Returns (scored, rows, market_support, threshold, sport_thresholds)."""
    from app.neuralbet.pipeline import ensemble_engine as e

    with _engine_lock:
        if cycle_aborted():
            raise RuntimeError("cycle aborted")
        market_support = _refresh_market_support()
        rows = _fetch_backtest_rows(limit=limit, since=None, sports=None, markets=None)
        if not rows:
            raise RuntimeError("no rows")

        items: list[dict] = []
        meta: list[dict] = []
        form_lookup = build_team_form_asof_lookup([
            {
                "event_id": r["event_id"],
                "factor_id": r["factor_id"],
                "parameter": r.get("parameter") or "",
                "market_prefix": r.get("market_prefix") or "",
                "team_1": r.get("team_1"),
                "team_2": r.get("team_2"),
                "sport_path": r.get("sport_path"),
                "is_win": r["is_win"],
                "finished_at": r["finished_at"],
            }
            for r in rows
        ])
        seen_events: set = set()
        event_rows: list[dict] = []
        for r in rows:
            eid = r["event_id"]
            if eid in seen_events:
                continue
            seen_events.add(eid)
            event_rows.append({
                "event_id": eid,
                "team_1": r.get("team_1"),
                "team_2": r.get("team_2"),
                "sport_path": r.get("sport_path"),
                "score_1": r.get("score_1"),
                "score_2": r.get("score_2"),
                "period_scores_json": r.get("period_scores_json"),
                "finished_at": r["finished_at"],
            })
        stats_lookup = build_team_stats_asof_lookup(event_rows)

        for r in rows:
            sample = row_to_sample(r)
            bet_key = (
                r["event_id"],
                int(sample["factor_id"] or 0),
                sample.get("parameter") or "",
                sample.get("market_prefix") or "",
            )
            t1_form, t2_form = form_lookup.get(bet_key, (None, None))
            if t1_form is not None:
                sample["team1_form_asof"] = t1_form
            if t2_form is not None:
                sample["team2_form_asof"] = t2_form
            stats_vec = stats_lookup.get(r["event_id"])
            if stats_vec is not None:
                sample["team_stats_asof"] = stats_vec
            view = build_model_input(sample, mode="backtest")
            if view is None:
                continue
            items.append(view)
            sport_name = (sample["sport_path"] or "").split("/")[0].strip() or "Другое"
            meta.append({
                "event_id": r["event_id"],
                "sport": sport_name,
                "sport_path": sample["sport_path"],
                "sport_key": sport_top(sample["sport_path"]),
                "coeff": float(view["current_coeff"]),
                "factor_id": sample["factor_id"],
                "label": sample["label"],
                "parameter": sample.get("parameter") or "",
                "market_prefix": sample.get("market_prefix") or "",
                "team_1": r.get("team_1") or sample.get("team_1") or "",
                "team_2": r.get("team_2") or sample.get("team_2") or "",
                "match_name": f"{r.get('team_1') or ''} — {r.get('team_2') or ''}".strip(" —"),
                "score_1": r.get("score_1"),
                "score_2": r.get("score_2"),
                "initial_coeff": (
                    sample.get("initial_coeff")
                    or r.get("initial_coefficient")
                    or view.get("initial_coeff")
                    or view.get("initial_coefficient")
                    or float(view["current_coeff"])
                ),
                "is_win": int(r["is_win"]),
                "trained_count": int(r.get("trained_count") or 0),
                "overround_close": r.get("overround_close"),
                "finished_at": r.get("finished_at"),
                "historical_prob": r["predicted_win_probability"],
                "historical_pred": r["predicted_win"],
                "market_key": market_label(int(sample["factor_id"] or 0)),
            })

        if not items:
            raise RuntimeError("no items after feature build")

        calibration_cutoff = str(rows[-1]["finished_at"])
        buckets = get_calibration_buckets(before=calibration_cutoff)
        e._apply_sport_threshold_floors()
        decision_threshold = float(e.decision_threshold)
        sport_decision_thresholds = dict(e.sport_decision_thresholds or {})

        predict_chunk = 2000
        raw_results: list = []
        for i in range(0, len(items), predict_chunk):
            raw_results.extend(e.predict_batch(items[i : i + predict_chunk]))

        scored: list[dict] = []
        for m, res in zip(meta, raw_results):
            win_prob, _error_rate, _lgb, _torch, decision_prob, stake_logit, _exp = res
            scored.append({
                **m,
                "raw_win_prob": win_prob,
                "decision_prob": decision_prob,
                "stake_logit": stake_logit,
            })

        return (
            scored,
            rows,
            market_support,
            decision_threshold,
            sport_decision_thresholds,
        )


def filter_rows(rows: list[Any], sport_set: frozenset[str], market_set: frozenset[str]) -> list[Any]:
    out = []
    for r in rows:
        sp = sport_top(r.get("sport_path"))
        mk = market_label(int(r.get("factor_id") or 0))
        if sp in sport_set and mk in market_set:
            out.append(r)
    return out


def eval_combo(
    scored: list[dict],
    rows: list[Any],
    market_support: dict,
    decision_threshold: float,
    sport_decision_thresholds: dict,
    sports: list[str],
    markets: list[str],
) -> dict[str, Any]:
    sport_set = frozenset(sports)
    market_set = frozenset(markets)
    filtered = [
        s for s in scored
        if s.get("sport_key") in sport_set and s.get("market_key") in market_set
    ]
    filtered_rows = filter_rows(rows, sport_set, market_set)
    if not filtered:
        return {
            "wf_roi": None,
            "wf_roi_lo": None,
            "wf_bets": 0,
            "core_pass": False,
            "samples": 0,
        }

    set_enabled_sports(sports)
    set_enabled_markets(markets)
    buckets = get_calibration_buckets()
    records, _ = _records_from_scored(
        filtered,
        buckets,
        decision_threshold,
        sport_decision_thresholds,
        market_support,
        apply_admin=True,
    )
    wf = _walk_forward_eval(
        filtered,
        filtered_rows,
        decision_threshold,
        sport_decision_thresholds,
        market_support,
        apply_admin=True,
    )
    wf_combined = (wf or {}).get("combined") if wf else None
    stake = ((wf_combined or {}).get("stake_policy") or {}).get("current") or {}
    prob = ((wf_combined or {}).get("probability") or {}).get("current") or {}
    market_brier = (wf_combined or {}).get("market_brier")
    brier = prob.get("brier")
    bets = stake.get("flat_bets") or stake.get("bets") or 0
    roi = stake.get("roi_pct")
    roi_lo = stake.get("roi_pct_lo")
    core = (
        bets >= 40
        and (roi or -999) > 0
        and (roi_lo or -999) > 0
        and brier is not None
        and market_brier is not None
        and float(brier) < float(market_brier)
    )
    return {
        "wf_roi": roi,
        "wf_roi_lo": roi_lo,
        "wf_roi_hi": stake.get("roi_pct_hi"),
        "wf_bets": bets,
        "wf_brier": brier,
        "wf_market_brier": market_brier,
        "brier_beats_market": (
            brier is not None and market_brier is not None and float(brier) < float(market_brier)
        ),
        "core_pass": core,
        "samples": len(filtered),
        "overall": _agg_group(records),
    }


def main() -> int:
    combos = [(list(s), list(m)) for s in subsets(ALL_SPORTS) for m in subsets(ALL_MARKETS)]
    print(f"Scoring once (limit={LIMIT}), then {len(combos)} combos…", flush=True)
    t0 = time.time()
    scored, rows, ms, dt, sdt = score_all(LIMIT)
    print(f"  scored {len(scored)} in {round(time.time()-t0, 1)}s", flush=True)

    results: list[dict] = []
    for i, (sports, markets) in enumerate(combos, 1):
        m = eval_combo(scored, rows, ms, dt, sdt, sports, markets)
        results.append({"sports": sports, "markets": markets, **m})
        if i % 100 == 0:
            print(f"  aggregated {i}/{len(combos)}", flush=True)

    ranked = sorted(
        results,
        key=lambda x: (
            x.get("core_pass") or False,
            x.get("wf_roi_lo") if x.get("wf_roi_lo") is not None else -9999,
            x.get("wf_roi") if x.get("wf_roi") is not None else -9999,
            x.get("wf_bets") or 0,
        ),
        reverse=True,
    )
    out = {
        "limit": LIMIT,
        "scored_n": len(scored),
        "combinations": len(combos),
        "core_pass_count": sum(1 for r in results if r.get("core_pass")),
        "elapsed_s": round(time.time() - t0, 1),
        "best": ranked[0] if ranked else None,
        "top20": ranked[:20],
        "all": ranked,
    }
    path = "/app/data/models/grid_search_exhaustive.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nSaved {path}", flush=True)
    b = ranked[0]
    print(f"BEST: sports={b['sports']} markets={b['markets']}", flush=True)
    print(
        f"  WF ROI={b['wf_roi']}% CI lo={b['wf_roi_lo']}% bets={b['wf_bets']} core={b['core_pass']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
