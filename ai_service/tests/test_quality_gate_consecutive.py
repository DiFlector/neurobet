"""Quality gate consecutive_passes must count core metric passes, not final pass."""
from __future__ import annotations

import importlib.util
import os
import sys

_AI_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_QG_PATH = os.path.join(_AI_ROOT, "app", "neuralbet", "quality_gate.py")


def _load_quality_gate():
    """Load quality_gate.py without importing app.neuralbet (avoids torch)."""
    spec = importlib.util.spec_from_file_location("neurobet_quality_gate", _QG_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


qg = _load_quality_gate()
evaluate_quality_gate = qg.evaluate_quality_gate


def _passing_walk_forward_block(**overrides):
    stake = {
        "bets": 137,
        "flat_bets": 137,
        "roi_pct": 20.2,
        "roi_pct_lo": 5.3,
        "roi_pct_hi": 33.3,
        "win_rate_pct": 67.2,
        "break_even_pct": 55.6,
    }
    stake.update(overrides)
    return {
        "probability": {
            "current": {"brier": 0.1941},
            "market_raw": {"brier": 0.2154},
        },
        "stake_policy": {"current": stake},
        "market_brier": 0.2154,
    }


def _result(generated_at: str, *, samples: int = 80000, walk_forward=None):
    return {
        "generated_at": generated_at,
        "samples_evaluated": samples,
        "samples_requested": samples,
        "walk_forward": walk_forward or _passing_walk_forward_block(),
        "overall": _passing_walk_forward_block(),
    }


def _history_row(generated_at: str, *, pass_: bool, samples: int = 80000, **metric_overrides):
    metrics = {
        "bets": 138,
        "roi_pct": 19.0,
        "roi_pct_lo": 3.8,
        "win_rate_pct": 66.7,
        "break_even_pct": 55.8,
        "brier": 0.1939,
        "market_brier": 0.2154,
        "samples_evaluated": samples,
        "consecutive_passes": 1,
        "consecutive_required": 2,
    }
    metrics.update(metric_overrides)
    return {
        "generated_at": generated_at,
        "samples_evaluated": samples,
        "samples_requested": samples,
        "overall": _passing_walk_forward_block(),
        "quality_gate": {
            "enabled": True,
            "pass": pass_,
            "eval_slice": "walk_forward",
            "reasons": [] if pass_ else ["consecutive_passes 1<2 (comparable sample size)"],
            "metrics": metrics,
        },
    }


def test_consecutive_uses_core_metrics_not_prior_pass_flag():
    current = _result("2026-08-20T15:30:44+03:00")
    prior = _history_row(
        "2026-08-20T15:29:27+03:00",
        pass_=False,  # failed only because of consecutive — must still count
    )
    gate = evaluate_quality_gate(current, history=[prior])
    assert gate["pass"] is True
    assert gate["metrics"]["consecutive_passes"] == 2
    assert gate["reasons"] == []


def test_live_reeval_skips_self_in_history():
    current = _result("2026-08-20T15:30:44+03:00")
    self_row = _history_row("2026-08-20T15:30:44+03:00", pass_=False)
    older_fail = _history_row(
        "2026-08-20T14:00:00+03:00",
        pass_=False,
        roi_pct=-1.0,
        roi_pct_lo=-10.0,
    )
    gate = evaluate_quality_gate(current, history=[self_row, older_fail])
    assert gate["pass"] is False
    assert gate["metrics"]["consecutive_passes"] == 1
    assert any("consecutive_passes" in r for r in gate["reasons"])


def test_prior_core_fail_breaks_streak():
    current = _result("2026-08-20T15:30:44+03:00")
    prior = _history_row(
        "2026-08-20T15:00:00+03:00",
        pass_=False,
        roi_pct_lo=-2.0,
    )
    prior["quality_gate"]["reasons"] = ["ROI CI lo -2.0"]
    gate = evaluate_quality_gate(current, history=[prior])
    assert gate["pass"] is False
    assert gate["metrics"]["consecutive_passes"] == 1


if __name__ == "__main__":
    test_consecutive_uses_core_metrics_not_prior_pass_flag()
    test_live_reeval_skips_self_in_history()
    test_prior_core_fail_breaks_streak()
    print("ok")
