"""Pure DeepSeek AND helpers for backtest walk-forward (no torch / DB)."""
from __future__ import annotations

from typing import Any, Dict, List


def record_llm_key(rec: Dict[str, Any]) -> str:
    return (
        f"{rec.get('event_id')}:{rec.get('factor_id')}:"
        f"{rec.get('parameter') or ''}:"
        f"{(rec.get('market_prefix') or '').strip()}"
    )


def apply_llm_and_to_records(
    records: List[Dict[str, Any]],
    decisions: Dict[str, int],
    *,
    fail_closed: bool = True,
) -> List[Dict[str, Any]]:
    """Live-parity AND: current_pred stays 1 only if DeepSeek approved.

    Unevaluated stake candidates are vetoed when ``fail_closed`` (matches
    ``NEURALBET_LLM_BATCH_REQUIRED``). Does not re-promote a second market
    on the same event after a veto — live also does not re-rank overflow.
    """
    out: List[Dict[str, Any]] = []
    for rec in records:
        clone = dict(rec)
        if int(clone.get("current_pred") or 0) != 1:
            out.append(clone)
            continue
        key = record_llm_key(rec)
        if key in decisions:
            flag = 1 if int(decisions[key]) == 1 else 0
            clone["llm_batch_decision"] = flag
            if flag != 1:
                clone["current_pred"] = 0
        elif fail_closed:
            clone["llm_batch_decision"] = 0
            clone["llm_unevaluated"] = True
            clone["current_pred"] = 0
        out.append(clone)
    return out
