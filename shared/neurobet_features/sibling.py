"""Sibling probability coherence (soft sum-to-1 + 2-way EV veto).

Live inference and backtest share this helper so serve / bot Kelly / backtest
stay in parity. Training uses the same overround_group_key grouping via the
paired-market loss in model.py.
"""
from __future__ import annotations

from typing import Any, Dict, List, MutableMapping

from neurobet_filters import MIN_BET_EDGE_PCT, MARKET_SHRINK, shrink_p_toward_market

from .overround import OVERROUND_EXPECTED_SIZE, overround_group_key


def apply_sibling_coherence(
    preds: List[MutableMapping[str, Any]],
    *,
    min_edge_pct: float = MIN_BET_EDGE_PCT,
    p_key: str = "calibrated_p",
    coeff_key: str = "coeff",
    predicted_win_key: str = "predicted_win",
) -> Dict[str, Any]:
    """Soft-renormalize complete sibling sets; veto dual-edge 2-way totals.

    Mutates `preds` in place. Each item needs ``event_id``, ``factor_id``,
    ``parameter``, ``market_prefix``, calibrated probability in ``[0, 1]``
    (``p_key``), and ``coeff``. Sets ``predicted_win`` from EV after renorm
    (Objective B: verdict = EV on post-coherence calibrated p).

    For complete 2-way totals where both poles still clear ``min_edge_pct``
    after renorm, keeps only the higher-EV side (coherence veto).
    """
    groups: Dict[Any, List[int]] = {}
    for i, row in enumerate(preds):
        gk = overround_group_key(
            row.get("factor_id"),
            str(row.get("parameter") or ""),
            str(row.get("market_prefix") or ""),
        )
        if gk is None:
            continue
        groups.setdefault((row.get("event_id"), gk), []).append(i)

    sum_abs_err = 0.0
    n_complete = 0
    for (_eid, gk), idxs in groups.items():
        need = OVERROUND_EXPECTED_SIZE.get(gk[0], 0)
        if not need or len(idxs) != need:
            continue
        probs = [max(0.0, float(preds[i].get(p_key) or 0.0)) for i in idxs]
        total = sum(probs)
        n_complete += 1
        sum_abs_err += abs(total - 1.0)
        if total > 1e-12:
            for i, idx in enumerate(idxs):
                preds[idx][p_key] = probs[i] / total

    if MARKET_SHRINK > 0:
        for row in preds:
            coeff = float(row.get(coeff_key) or 0.0)
            row[p_key] = shrink_p_toward_market(float(row.get(p_key) or 0.0), coeff)
        for (_eid, gk), idxs in groups.items():
            need = OVERROUND_EXPECTED_SIZE.get(gk[0], 0)
            if not need or len(idxs) != need:
                continue
            probs = [max(0.0, float(preds[i].get(p_key) or 0.0)) for i in idxs]
            total = sum(probs)
            if total > 1e-12:
                for i, idx in enumerate(idxs):
                    preds[idx][p_key] = probs[i] / total

    for row in preds:
        prob = float(row.get(p_key) or 0.0)
        coeff = float(row.get(coeff_key) or 0.0)
        ev = (prob * coeff - 1.0) * 100.0
        row["expected_roi"] = ev
        row[predicted_win_key] = 1 if ev >= min_edge_pct else 0

    veto_count = 0
    for (_eid, gk), idxs in groups.items():
        if gk[0] != "total":
            continue
        need = OVERROUND_EXPECTED_SIZE.get(gk[0], 0)
        if not need or len(idxs) != need:
            continue
        edged = [i for i in idxs if int(preds[i].get(predicted_win_key) or 0) == 1]
        if len(edged) < 2:
            continue
        edged.sort(
            key=lambda i: float(preds[i].get("expected_roi") or 0.0),
            reverse=True,
        )
        for i in edged[1:]:
            preds[i][predicted_win_key] = 0
            veto_count += 1

    return {
        "sibling_sum_mae": (
            round(sum_abs_err / n_complete, 6) if n_complete else None
        ),
        "coherence_veto_count": veto_count,
        "complete_groups": n_complete,
    }
