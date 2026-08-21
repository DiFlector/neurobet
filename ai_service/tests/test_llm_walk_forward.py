"""DeepSeek AND filter for walk-forward / quality_gate."""
from __future__ import annotations

import importlib.util
import os
import sys

_AI_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_GATE_PATH = os.path.join(_AI_ROOT, "app", "neuralbet", "llm_gate.py")


def _load_llm_gate():
    """Load llm_gate.py without importing app.neuralbet (avoids torch)."""
    spec = importlib.util.spec_from_file_location("neurobet_llm_gate", _GATE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_llm_gate()
apply_llm_and_to_records = _mod.apply_llm_and_to_records
record_llm_key = _mod.record_llm_key


def _rec(eid: int, pred: int = 1, **extra):
    row = {
        "event_id": eid,
        "factor_id": 10,
        "parameter": "",
        "market_prefix": "",
        "current_pred": pred,
        "is_win": 1,
        "coeff": 1.8,
    }
    row.update(extra)
    return row


def test_record_llm_key_strips_prefix():
    assert record_llm_key(_rec(7, market_prefix="  tot  ")) == "7:10::tot"


def test_and_keeps_approved_vetoes_rest_fail_closed():
    records = [_rec(1), _rec(2), _rec(3, pred=0)]
    decisions = {record_llm_key(_rec(1)): 1, record_llm_key(_rec(2)): 0}
    out = apply_llm_and_to_records(records, decisions, fail_closed=True)
    assert [r["current_pred"] for r in out] == [1, 0, 0]
    assert out[0]["llm_batch_decision"] == 1
    assert out[1]["llm_batch_decision"] == 0
    assert "llm_unevaluated" not in out[1]


def test_unevaluated_fail_closed():
    records = [_rec(1), _rec(2)]
    decisions = {record_llm_key(_rec(1)): 1}
    out = apply_llm_and_to_records(records, decisions, fail_closed=True)
    assert out[0]["current_pred"] == 1
    assert out[1]["current_pred"] == 0
    assert out[1]["llm_unevaluated"] is True


def test_unevaluated_fail_open_keeps_model_pred():
    records = [_rec(1), _rec(2)]
    decisions = {record_llm_key(_rec(1)): 1}
    out = apply_llm_and_to_records(records, decisions, fail_closed=False)
    assert [r["current_pred"] for r in out] == [1, 1]


def test_does_not_promote_non_stake_rows():
    records = [_rec(1, pred=0)]
    decisions = {record_llm_key(_rec(1)): 1}
    out = apply_llm_and_to_records(records, decisions, fail_closed=True)
    assert out[0]["current_pred"] == 0
    assert "llm_batch_decision" not in out[0]
