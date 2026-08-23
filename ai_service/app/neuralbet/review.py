"""
Condensed backtest review for agents and the admin panel.

Turns a full backtest JSON (+ optional raw records) into a single structured
`agent_review` block: slice summaries, walk-forward stability, live funnel,
head-alignment diagnostics, delta vs previous run, and actionable flags.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from neurobet_filters import MIN_BET_COEFF, MIN_BET_EDGE_PCT, MAX_BET_COEFF

from app.config import MODEL_DIR
from app.neuralbet.quality_gate import evaluate_quality_gate


def _quality_gate(result: Dict[str, Any]) -> Dict[str, Any]:
    if result.get("quality_gate"):
        return result["quality_gate"]
    return evaluate_quality_gate(result)


def _stake_current(block: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not block:
        return {}
    return ((block.get("stake_policy") or {}).get("current")) or {}


def _prob_current(block: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not block:
        return {}
    return ((block.get("probability") or {}).get("current")) or {}


def _compact_slice(name: str, block: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not block:
        return None
    stake = _stake_current(block)
    prob = _prob_current(block)
    legacy = block.get("current") or {}
    market_brier = block.get("market_brier")
    brier = prob.get("brier") if prob.get("brier") is not None else legacy.get("brier")
    return {
        "slice": name,
        "evaluated": block.get("evaluated"),
        "bets": stake.get("flat_bets") if stake.get("flat_bets") is not None else stake.get("bets"),
        "flat_bets": stake.get("flat_bets") if stake.get("flat_bets") is not None else stake.get("bets"),
        "kelly_bets": stake.get("kelly_bets"),
        "roi_pct": stake.get("roi_pct"),
        "roi_pct_lo": stake.get("roi_pct_lo"),
        "roi_pct_hi": stake.get("roi_pct_hi"),
        "win_rate_pct": stake.get("win_rate_pct"),
        "break_even_pct": stake.get("break_even_pct"),
        "bankroll_roi_pct": stake.get("bankroll_roi_pct"),
        "brier": brier,
        "market_brier": market_brier,
        "brier_beats_market": (
            brier is not None and market_brier is not None and float(brier) < float(market_brier)
        ),
        "accuracy_pct": legacy.get("accuracy_pct"),
        "verdict_precision_pct": ((block.get("verdict") or {}).get("current") or {}).get("precision_pct"),
    }


def build_funnel(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(records)
    if n == 0:
        return {"evaluated": 0}

    verdict_pos = sum(1 for r in records if int(r.get("current_verdict") or 0) == 1)
    ev_pos = sum(1 for r in records if float(r.get("current_expected_roi") or 0) >= MIN_BET_EDGE_PCT)
    in_band = sum(
        1 for r in records
        if MIN_BET_COEFF <= float(r.get("coeff") or 0) <= MAX_BET_COEFF
    )
    verdict_and_ev = sum(
        1 for r in records
        if int(r.get("current_verdict") or 0) == 1
        and float(r.get("current_expected_roi") or 0) >= MIN_BET_EDGE_PCT
    )
    stake_candidates = sum(1 for r in records if int(r.get("stake_candidate") or 0) == 1)
    final_bets = sum(1 for r in records if int(r.get("current_pred") or 0) == 1)

    return {
        "evaluated": n,
        "in_coeff_band": in_band,
        "verdict_positive": verdict_pos,
        "ev_positive": ev_pos,
        "verdict_and_ev": verdict_and_ev,
        "stake_candidates": stake_candidates,
        "final_bets": final_bets,
        "dedupe_dropped": max(0, stake_candidates - final_bets),
        "verdict_positive_pct": round(verdict_pos / n * 100.0, 1),
        "final_bets_pct": round(final_bets / n * 100.0, 2),
    }


def build_reliability(records: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Predicted-p vs actual win rate (calibration). current_prob is 0–100."""
    if not records:
        return []
    edges = [50.0, 55.0, 60.0, 65.0, 70.0, 80.0, 100.0]
    out: List[Dict[str, Any]] = []
    for i, hi in enumerate(edges):
        lo = 0.0 if i == 0 else edges[i - 1]
        hit: List[Dict[str, Any]] = []
        for r in records:
            raw = r.get("current_prob")
            if raw is None:
                continue
            p = float(raw)
            if i == len(edges) - 1:
                if p >= lo:
                    hit.append(r)
            elif i == 0:
                if p < hi:
                    hit.append(r)
            elif lo <= p < hi:
                hit.append(r)
        n = len(hit)
        if n == 0:
            continue
        mean_p = sum(float(r["current_prob"]) for r in hit) / n
        emp = 100.0 * sum(int(r.get("is_win") or 0) for r in hit) / n
        market_vals = [float(r["market_prob"]) for r in hit if r.get("market_prob") is not None]
        mean_mkt = (sum(market_vals) / len(market_vals)) if market_vals else None
        gap = emp - mean_p
        out.append({
            "lo_pct": lo,
            "hi_pct": hi,
            "n": n,
            "mean_pred_pct": round(mean_p, 1),
            "empirical_win_pct": round(emp, 1),
            "mean_market_pct": round(mean_mkt, 1) if mean_mkt is not None else None,
            "gap_pct": round(gap, 1),
        })
    return out


def build_head_alignment(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    verdict_yes_ev_no = 0
    verdict_no_ev_yes = 0
    for r in records:
        verdict = int(r.get("current_verdict") or 0)
        ev_ok = float(r.get("current_expected_roi") or 0) >= MIN_BET_EDGE_PCT
        if verdict == 1 and not ev_ok:
            verdict_yes_ev_no += 1
        if verdict == 0 and ev_ok:
            verdict_no_ev_yes += 1
    n = len(records) or 1
    return {
        "verdict_yes_ev_no": verdict_yes_ev_no,
        "verdict_no_ev_yes": verdict_no_ev_yes,
        "verdict_yes_ev_no_pct": round(verdict_yes_ev_no / n * 100.0, 2),
        "verdict_no_ev_yes_pct": round(verdict_no_ev_yes / n * 100.0, 2),
        "min_edge_pct": MIN_BET_EDGE_PCT,
    }


def build_walk_forward_stability(result: Dict[str, Any]) -> Dict[str, Any]:
    folds = result.get("walk_forward_folds") or []
    if not folds:
        return {"folds": 0, "negative_roi_folds": 0, "stable": None}

    fold_rows: List[Dict[str, Any]] = []
    negative = 0
    for f in folds:
        stake = _stake_current(f)
        roi = stake.get("roi_pct")
        roi_lo = stake.get("roi_pct_lo")
        if roi is not None and float(roi) <= 0:
            negative += 1
        fold_rows.append({
            "fold": f.get("fold"),
            "events": f.get("events"),
            "bets": stake.get("bets"),
            "roi_pct": roi,
            "roi_pct_lo": roi_lo,
            "brier": _prob_current(f).get("brier"),
            "passes_ci": roi_lo is not None and float(roi_lo) > 0,
        })

    return {
        "folds": len(folds),
        "negative_roi_folds": negative,
        "stable": negative == 0 and all(r.get("passes_ci") for r in fold_rows if r.get("bets")),
        "folds_detail": fold_rows,
    }


def _compact_by_sport(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in result.get("by_sport") or []:
        stake = _stake_current(row)
        prob = _prob_current(row)
        rows.append({
            "sport": row.get("sport"),
            "evaluated": row.get("evaluated"),
            "bets": stake.get("bets"),
            "roi_pct": stake.get("roi_pct"),
            "roi_pct_lo": stake.get("roi_pct_lo"),
            "brier": prob.get("brier"),
            "market_brier": row.get("market_brier"),
            "brier_beats_market": (
                prob.get("brier") is not None
                and row.get("market_brier") is not None
                and float(prob["brier"]) < float(row["market_brier"])
            ),
        })
    return sorted(rows, key=lambda x: -(x.get("bets") or 0))


def _compact_by_market(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in result.get("by_market") or []:
        stake = _stake_current(row)
        prob = _prob_current(row)
        rows.append({
            "market": row.get("market"),
            "evaluated": row.get("evaluated"),
            "bets": stake.get("bets"),
            "roi_pct": stake.get("roi_pct"),
            "roi_pct_lo": stake.get("roi_pct_lo"),
            "brier": prob.get("brier"),
        })
    return sorted(rows, key=lambda x: -(x.get("bets") or 0))


def _compact_oos_ablation_slice(block: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not block:
        return None
    stake = _stake_current(block)
    prob = _prob_current(block)
    bets = stake.get("flat_bets") if stake.get("flat_bets") is not None else stake.get("bets")
    return {
        "evaluated": block.get("evaluated"),
        "bets": bets,
        "roi_pct": stake.get("roi_pct"),
        "roi_pct_lo": stake.get("roi_pct_lo"),
        "win_rate_pct": stake.get("win_rate_pct"),
        "brier": prob.get("brier"),
        "market_brier": block.get("market_brier"),
    }


def _compact_oos_ablation(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw = result.get("oos_ablation")
    if not isinstance(raw, dict) or not raw:
        return None
    out: Dict[str, Any] = {}
    for key, block in raw.items():
        compact = _compact_oos_ablation_slice(block)
        if compact is not None:
            out[key] = compact
    return out or None


def _delta_vs_previous(result: Dict[str, Any], history: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not history:
        return None

    cur_samples = result.get("samples_requested") or result.get("samples_evaluated")
    prev: Optional[Dict[str, Any]] = None
    for row in history:
        if row.get("generated_at") == result.get("generated_at"):
            continue
        prev_samples = row.get("samples_evaluated") or row.get("samples_requested")
        if _samples_comparable(cur_samples, prev_samples):
            prev = row
            break
    if prev is None:
        prev = history[0]

    cur_stake = _stake_current(result.get("overall"))
    prev_stake = _stake_current(prev.get("overall"))
    cur_legacy = (result.get("overall") or {}).get("current") or {}
    prev_legacy = (prev.get("overall") or {}).get("current") or {}

    def _d(cur, prev_v):
        if cur is None or prev_v is None:
            return None
        return round(float(cur) - float(prev_v), 2)

    return {
        "previous_at": prev.get("generated_at"),
        "previous_samples": prev.get("samples_evaluated") or prev.get("samples_requested"),
        "comparable_sample_size": _samples_comparable(cur_samples, prev.get("samples_evaluated")),
        "roi_pct": _d(cur_stake.get("roi_pct"), prev_stake.get("roi_pct")),
        "roi_pct_lo": _d(cur_stake.get("roi_pct_lo"), prev_stake.get("roi_pct_lo")),
        "bets": _d(cur_stake.get("bets"), prev_stake.get("bets")),
        "brier": _d(cur_legacy.get("brier"), prev_legacy.get("brier")),
        "accuracy_pct": _d(cur_legacy.get("accuracy_pct"), prev_legacy.get("accuracy_pct")),
        "quality_gate_pass": {
            "current": (result.get("quality_gate") or {}).get("pass"),
            "previous": (prev.get("quality_gate") or {}).get("pass"),
        },
    }


def _samples_comparable(a: Optional[int], b: Optional[int]) -> bool:
    from app.neuralbet.quality_gate import QUALITY_GATE_SAMPLE_TOLERANCE

    if a is None or b is None or a <= 0 or b <= 0:
        return False
    lo, hi = min(a, b), max(a, b)
    return (hi - lo) / hi <= QUALITY_GATE_SAMPLE_TOLERANCE


def _edge_verdict(result: Dict[str, Any], gate: Dict[str, Any], wf: Dict[str, Any]) -> str:
    oos_slice = _compact_slice("oos_never_train", result.get("oos_never_train"))
    wf_slice = _compact_slice("walk_forward", result.get("walk_forward"))
    gate_slice = gate.get("eval_slice")
    ref = None
    if gate_slice == "walk_forward":
        ref = wf_slice
    elif gate_slice == "oos_never_train":
        ref = oos_slice
    elif gate_slice == "overall":
        ref = _compact_slice("overall", result.get("overall"))
    ref = ref or wf_slice or oos_slice or _compact_slice("overall", result.get("overall"))

    if not ref:
        return "unknown"
    if gate.get("pass"):
        if wf.get("folds") and wf.get("stable") is False:
            return "promising"
        return "likely"
    if ref.get("brier_beats_market") and ref.get("roi_pct") is not None and float(ref["roi_pct"]) > 0:
        if ref.get("roi_pct_lo") is not None and float(ref["roi_pct_lo"]) > 0:
            return "promising"
        return "unproven"
    if ref.get("brier_beats_market"):
        return "calibration_only"
    if wf.get("negative_roi_folds", 0) >= max(2, (wf.get("folds") or 0) // 2):
        return "none"
    return "none"


def _build_flags(
    result: Dict[str, Any],
    gate: Dict[str, Any],
    wf: Dict[str, Any],
    alignment: Optional[Dict[str, Any]],
    delta: Optional[Dict[str, Any]],
    reliability: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    flags: List[Dict[str, str]] = []

    if not gate.get("pass"):
        for reason in gate.get("reasons") or []:
            flags.append({"severity": "block", "code": "quality_gate", "message": reason})

    if wf.get("folds") and wf.get("negative_roi_folds", 0) > 0:
        flags.append({
            "severity": "warning",
            "code": "wf_fold_negative_roi",
            "message": f"{wf['negative_roi_folds']}/{wf['folds']} walk-forward folds have ROI ≤ 0",
        })

    blend = (result.get("config") or {}).get("blend_weight")
    market_w = (result.get("config") or {}).get("market_weight")
    if blend is not None and float(blend) <= 0.01:
        flags.append({
            "severity": "info",
            "code": "gru_blend_zero",
            "message": f"GRU blend_weight≈0 — probability mix is LGB+market ({market_w})",
        })

    if alignment and alignment.get("verdict_yes_ev_no_pct", 0) > 5:
        flags.append({
            "severity": "warning",
            "code": "head_misalignment",
            "message": (
                f"{alignment['verdict_yes_ev_no_pct']}% rows: decision=yes but EV<{MIN_BET_EDGE_PCT}%"
            ),
        })

    lgb_off = (result.get("config") or {}).get("lgb_disable_team_features")
    if lgb_off:
        flags.append({
            "severity": "info",
            "code": "lgb_team_features_off",
            "message": "LightGBM team hash features disabled (ablation mode)",
        })

    for row in result.get("oos_by_market") or []:
        stake = ((row.get("stake_policy") or {}).get("current")) or {}
        bets = int(stake.get("flat_bets") if stake.get("flat_bets") is not None else stake.get("bets") or 0)
        roi = stake.get("roi_pct")
        if row.get("market") == "total_under" and bets >= 10 and roi is not None and float(roi) < 0:
            flags.append({
                "severity": "warning",
                "code": "oos_total_under_negative",
                "message": f"OOS total_under: ROI {roi}% on {bets} bets (CI lo {stake.get('roi_pct_lo')})",
            })

    oos_ablation = result.get("oos_ablation") or {}
    tt_over = oos_ablation.get("table_tennis_x_total_over") if isinstance(oos_ablation, dict) else None
    if tt_over:
        stake = _stake_current(tt_over)
        bets = int(stake.get("flat_bets") if stake.get("flat_bets") is not None else stake.get("bets") or 0)
        roi = stake.get("roi_pct")
        roi_lo = stake.get("roi_pct_lo")
        if bets >= 10 and roi is not None:
            if float(roi) > 0 and roi_lo is not None and float(roi_lo) > 0:
                flags.append({
                    "severity": "info",
                    "code": "oos_tt_total_over_edge",
                    "message": (
                        f"OOS настольный теннис × total_over: ROI {roi}% "
                        f"(CI lo {roi_lo}) on {bets} bets"
                    ),
                })
            elif float(roi) <= 0:
                flags.append({
                    "severity": "warning",
                    "code": "oos_tt_total_over_negative",
                    "message": (
                        f"OOS настольный теннис × total_over: ROI {roi}% "
                        f"(CI lo {roi_lo}) on {bets} bets — in-sample pocket not confirmed OOS"
                    ),
                })

    try:
        tune_path = os.path.join(MODEL_DIR, "last_tune.json")
        last_tune = None
        if os.path.exists(tune_path):
            with open(tune_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            last_tune = loaded if isinstance(loaded, dict) else None
    except Exception:
        last_tune = None
    wf_slice = _compact_slice("walk_forward", result.get("walk_forward"))
    if last_tune and wf_slice and last_tune.get("val_roi_pct") is not None and wf_slice.get("roi_pct") is not None:
        tuner_roi = float(last_tune["val_roi_pct"])
        wf_roi = float(wf_slice["roi_pct"])
        tuner_bets = int(last_tune.get("val_bets") or 0)
        delta_pp = abs(tuner_roi - wf_roi)
        if delta_pp >= 10 and tuner_bets > 0:
            flags.append({
                "severity": "warning",
                "code": "fixed_val_tuner_vs_walk_forward",
                "message": (
                    f"Fixed-val tuner ROI {tuner_roi}% on {tuner_bets} bets vs "
                    f"walk_forward ROI {wf_roi}% (Δ {delta_pp:.1f} pp) — "
                    f"do not trust tuner ROI for live gate"
                ),
            })

    if policy_ablation := result.get("policy_ablation_oos"):
        best = max(
            policy_ablation.items(),
            key=lambda kv: float(kv[1].get("roi_pct") or -999),
        )
        flags.append({
            "severity": "info",
            "code": "policy_ablation_oos",
            "message": (
                f"OOS policy ablation best: {best[0]} ROI {best[1].get('roi_pct')}% "
                f"({best[1].get('bets')} bets, CI lo {best[1].get('roi_pct_lo')})"
            ),
        })

    if delta and delta.get("brier") is not None and float(delta["brier"]) > 0.005:
        flags.append({
            "severity": "warning",
            "code": "brier_regression",
            "message": f"Brier worsened vs previous run (+{delta['brier']})",
        })

    for sport in _compact_by_sport(result):
        if (sport.get("bets") or 0) >= 20 and sport.get("roi_pct") is not None:
            if float(sport["roi_pct"]) > 5 and sport.get("brier_beats_market"):
                flags.append({
                    "severity": "info",
                    "code": "sport_edge_candidate",
                    "message": f"{sport['sport']}: ROI {sport['roi_pct']}%, Brier beats market",
                })

    if reliability:
        worst = min(
            (b for b in reliability if int(b.get("n") or 0) >= 80),
            key=lambda b: float(b.get("gap_pct") or 0),
            default=None,
        )
        if worst is not None and float(worst.get("gap_pct") or 0) <= -5:
            flags.append({
                "severity": "warning",
                "code": "overconfident_probs",
                "message": (
                    f"Reliability {worst['lo_pct']:.0f}–{worst['hi_pct']:.0f}%: "
                    f"pred {worst['mean_pred_pct']}% vs actual {worst['empirical_win_pct']}% "
                    f"(n={worst['n']})"
                ),
            })

    return flags


def _one_liner(edge: str, gate: Dict[str, Any], ref: Optional[Dict[str, Any]]) -> str:
    parts = [f"edge={edge}"]
    if gate.get("enabled"):
        parts.append("gate=" + ("pass" if gate.get("pass") else "fail"))
    if ref:
        parts.append(f"ROI={ref.get('roi_pct')}%")
        if ref.get("roi_pct_lo") is not None:
            parts.append(f"CIlo={ref.get('roi_pct_lo')}%")
        if ref.get("brier_beats_market"):
            parts.append("Brier<market")
    return "; ".join(parts)


def build_agent_review(
    result: Dict[str, Any],
    records: Optional[List[Dict[str, Any]]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    gate = _quality_gate(result)
    wf = build_walk_forward_stability(result)
    alignment = build_head_alignment(records) if records else None
    funnel = build_funnel(records) if records else None
    reliability = build_reliability(records) if records else []
    delta = _delta_vs_previous(result, history or []) if history else None

    slices = {
        k: v for k, v in {
            "overall": _compact_slice("overall", result.get("overall")),
            "in_sample": _compact_slice("in_sample", result.get("in_sample")),
            "oos_never_train": _compact_slice("oos_never_train", result.get("oos_never_train")),
            "walk_forward": _compact_slice("walk_forward", result.get("walk_forward")),
            "walk_forward_model_only": _compact_slice(
                "walk_forward_model_only", result.get("walk_forward_model_only"),
            ),
        }.items() if v
    }

    ref_name = gate.get("eval_slice")
    ref = (
        (slices.get(ref_name) if ref_name else None)
        or slices.get("walk_forward")
        or slices.get("oos_never_train")
        or slices.get("overall")
    )
    edge = _edge_verdict(result, gate, wf)

    policy_ablation = result.get("policy_ablation_oos")

    trend = "unknown"
    if delta and delta.get("roi_pct") is not None:
        if float(delta["roi_pct"]) > 2:
            trend = "improving"
        elif float(delta["roi_pct"]) < -2:
            trend = "degrading"
        else:
            trend = "stable"

    return {
        "schema_version": 1,
        "generated_at": result.get("generated_at"),
        "summary": {
            "edge_verdict": edge,
            "trend_vs_previous": trend,
            "quality_gate_pass": gate.get("pass"),
            "brier_beats_market": bool(ref and ref.get("brier_beats_market")),
            "one_liner": _one_liner(edge, gate, ref),
        },
        "slices": slices,
        "walk_forward_stability": wf,
        "funnel": funnel,
        "head_alignment": alignment,
        "reliability": reliability,
        "sibling_sum_mae": (result.get("sibling_coherence") or {}).get("sibling_sum_mae"),
        "coherence_veto_count": (result.get("sibling_coherence") or {}).get(
            "coherence_veto_count"
        ),
        "by_sport": _compact_by_sport(result),
        "by_market": _compact_by_market(result),
        "oos_by_market": [
            {
                "market": row.get("market"),
                "evaluated": row.get("evaluated"),
                "bets": (_stake_current(row).get("flat_bets")
                         if _stake_current(row).get("flat_bets") is not None
                         else _stake_current(row).get("bets")),
                "roi_pct": _stake_current(row).get("roi_pct"),
                "roi_pct_lo": _stake_current(row).get("roi_pct_lo"),
                "brier": _prob_current(row).get("brier"),
            }
            for row in (result.get("oos_by_market") or [])
        ],
        "oos_ablation": _compact_oos_ablation(result),
        "delta_vs_previous": delta,
        "policy_ablation_oos": policy_ablation,
        "walk_forward_meta": result.get("walk_forward_meta"),
        "flags": _build_flags(result, gate, wf, alignment, delta, reliability),
        "quality_gate": gate,
    }


def build_review_from_latest(
    latest: Optional[Dict[str, Any]],
    history: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    if not latest or latest.get("status") != "success":
        return None
    if latest.get("agent_review"):
        return latest["agent_review"]
    return build_agent_review(latest, records=None, history=history)
