"""Live quality gate: walk-forward edge checks for virtual bankroll staking.

Kept free of torch / DB so unit tests can import this module directly.
"""
from __future__ import annotations

import os
from neurobet_time import MOSCOW_TZ

BACKTEST_DEFAULT_LIMIT = int(os.getenv("NEURALBET_BACKTEST_DEFAULT_LIMIT", "80000"))
LIVE_QUALITY_GATE = os.getenv("NEURALBET_LIVE_QUALITY_GATE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
LIVE_QUALITY_MIN_BETS = int(os.getenv("NEURALBET_LIVE_QUALITY_MIN_BETS", "40"))
QUALITY_GATE_MIN_CONSECUTIVE = int(os.getenv("NEURALBET_QUALITY_GATE_MIN_CONSECUTIVE", "2"))
QUALITY_GATE_MIN_SAMPLES = int(os.getenv("NEURALBET_QUALITY_GATE_MIN_SAMPLES", str(BACKTEST_DEFAULT_LIMIT)))
QUALITY_GATE_MAX_AGE_HOURS = float(os.getenv("NEURALBET_QUALITY_GATE_MAX_AGE_HOURS", "12"))
QUALITY_GATE_SAMPLE_TOLERANCE = float(os.getenv("NEURALBET_QUALITY_GATE_SAMPLE_TOLERANCE", "0.25"))


def _gate_slice_flat_bets(block: Dict[str, Any]) -> int:
    stake = ((block.get("stake_policy") or {}).get("current")) or {}
    return int(
        stake.get("flat_bets")
        if stake.get("flat_bets") is not None
        else stake.get("bets")
        or 0
    )


def _gate_slice_metrics(result: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Primary gate slice: walk-forward OOS; never-train / overall as fallbacks.

    Prefer a later candidate when the preferred slice has fewer than
    LIVE_QUALITY_MIN_BETS and a fallback already clears that floor — otherwise
    catch-up can drain never-train to a handful of rows and falsely block live.
    """
    candidates: List[Tuple[str, Dict[str, Any]]] = []
    for name in ("walk_forward", "oos_never_train", "overall"):
        block = result.get(name)
        if block:
            candidates.append((name, block))
    if not candidates:
        return "overall", {}

    for name, block in candidates:
        if _gate_slice_flat_bets(block) >= LIVE_QUALITY_MIN_BETS:
            return name, block
    return candidates[0]


def _gate_core_metrics(eval_block: Dict[str, Any]) -> Dict[str, Any]:
    stake = ((eval_block.get("stake_policy") or {}).get("current")) or {}
    bets = int(stake.get("flat_bets") if stake.get("flat_bets") is not None else stake.get("bets") or 0)
    roi = stake.get("roi_pct")
    roi_lo = stake.get("roi_pct_lo")
    win_rate = stake.get("win_rate_pct")
    break_even = stake.get("break_even_pct")
    brier = ((eval_block.get("probability") or {}).get("current") or {}).get("brier")
    if brier is None:
        brier = (eval_block.get("current") or {}).get("brier")
    market = ((eval_block.get("probability") or {}).get("market_raw") or {}).get("brier")
    if market is None:
        market = eval_block.get("market_brier")
    return {
        "bets": bets,
        "roi_pct": roi,
        "roi_pct_lo": roi_lo,
        "win_rate_pct": win_rate,
        "break_even_pct": break_even,
        "brier": brier,
        "market_brier": market,
    }


def _gate_core_reasons(metrics: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    bets = int(metrics.get("bets") or 0)
    roi = metrics.get("roi_pct")
    roi_lo = metrics.get("roi_pct_lo")
    win_rate = metrics.get("win_rate_pct")
    break_even = metrics.get("break_even_pct")
    brier = metrics.get("brier")
    market = metrics.get("market_brier")
    if bets < LIVE_QUALITY_MIN_BETS:
        reasons.append(f"bets {bets}<{LIVE_QUALITY_MIN_BETS}")
    if roi is None or float(roi) <= 0:
        reasons.append(f"ROI {roi}")
    if roi_lo is not None and float(roi_lo) <= 0:
        reasons.append(f"ROI CI lo {roi_lo}")
    if win_rate is None or break_even is None or float(win_rate) <= float(break_even):
        reasons.append(f"win_rate {win_rate}≤break-even {break_even}")
    if brier is None or market is None or float(brier) >= float(market):
        reasons.append(f"Brier {brier}≥market {market}")
    return reasons


def _prior_base_gate_ok(prior: Dict[str, Any]) -> bool:
    """Whether a historical run cleared core gate checks (bets/ROI/CI/WR/Brier + samples).

    Ignores consecutive_passes and age — those are properties of the *current*
    evaluation, not of the prior run's edge signal. Using prior quality_gate.pass
    here creates a deadlock: a run that only failed consecutive can never seed
    the next run's streak.
    """
    prior_gate = prior.get("quality_gate")
    if isinstance(prior_gate, dict) and prior_gate.get("enabled") is False:
        return True

    if isinstance(prior_gate, dict) and prior_gate.get("metrics"):
        m = prior_gate["metrics"]
        core = _gate_core_reasons(
            {
                "bets": m.get("bets"),
                "roi_pct": m.get("roi_pct"),
                "roi_pct_lo": m.get("roi_pct_lo"),
                "win_rate_pct": m.get("win_rate_pct"),
                "break_even_pct": m.get("break_even_pct"),
                "brier": m.get("brier"),
                "market_brier": m.get("market_brier"),
            }
        )
        if core:
            return False
        samples = (
            m.get("samples_evaluated")
            or prior.get("samples_evaluated")
            or prior.get("samples_requested")
            or 0
        )
        return int(samples) >= QUALITY_GATE_MIN_SAMPLES

    # Legacy history rows without quality_gate — derive from available slices
    # (condensed history only stores overall, so this may use overall).
    _, block = _gate_slice_metrics(prior)
    if _gate_core_reasons(_gate_core_metrics(block)):
        return False
    samples = prior.get("samples_evaluated") or prior.get("samples_requested") or 0
    return int(samples) >= QUALITY_GATE_MIN_SAMPLES


def _backtest_age_hours(result: Dict[str, Any]) -> Optional[float]:
    raw = result.get("generated_at")
    if not raw:
        return None
    try:
        generated = datetime.fromisoformat(str(raw))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=MOSCOW_TZ)
        now = datetime.now(MOSCOW_TZ)
        return (now - generated.astimezone(MOSCOW_TZ)).total_seconds() / 3600.0
    except Exception:
        return None


def _samples_comparable(a: Optional[int], b: Optional[int]) -> bool:
    if a is None or b is None or a <= 0 or b <= 0:
        return False
    lo = min(a, b)
    hi = max(a, b)
    return (hi - lo) / hi <= QUALITY_GATE_SAMPLE_TOLERANCE


def evaluate_quality_gate(
    result: Dict[str, Any],
    history: Optional[List[Dict[str, Any]]] = None,
    *,
    check_age: bool = False,
) -> Dict[str, Any]:
    """Same checks pipeline._live_quality_skip_reason uses — embedded in backtest JSON."""
    if not LIVE_QUALITY_GATE:
        return {"enabled": False, "pass": True, "eval_slice": None, "reasons": [], "metrics": {}}

    eval_slice, eval_block = _gate_slice_metrics(result)
    metrics = _gate_core_metrics(eval_block)
    reasons = _gate_core_reasons(metrics)

    samples = result.get("samples_requested") or result.get("samples_evaluated") or 0
    if samples < QUALITY_GATE_MIN_SAMPLES:
        reasons.append(f"samples {samples}<{QUALITY_GATE_MIN_SAMPLES}")

    consecutive_required = max(1, QUALITY_GATE_MIN_CONSECUTIVE)
    consecutive_passes = 1 if not reasons else 0
    if history and consecutive_required > 1 and consecutive_passes == 1:
        prior_passes = 0
        current_at = result.get("generated_at")
        for prior in history:
            # Live re-eval passes history that already contains `result` as [0].
            if current_at and prior.get("generated_at") == current_at:
                continue
            prior_samples = prior.get("samples_evaluated") or prior.get("samples_requested") or 0
            if not _samples_comparable(samples, prior_samples):
                continue
            if _prior_base_gate_ok(prior):
                prior_passes += 1
            else:
                break
            if prior_passes >= consecutive_required - 1:
                break
        consecutive_passes = 1 + prior_passes
        if consecutive_passes < consecutive_required:
            reasons.append(
                f"consecutive_passes {consecutive_passes}<{consecutive_required} (comparable sample size)"
            )

    age_h = _backtest_age_hours(result) if check_age else None
    if check_age and age_h is not None and age_h > QUALITY_GATE_MAX_AGE_HOURS:
        reasons.append(f"backtest age {age_h:.1f}h>{QUALITY_GATE_MAX_AGE_HOURS}h")

    return {
        "enabled": True,
        "pass": len(reasons) == 0,
        "eval_slice": eval_slice,
        "reasons": reasons,
        "metrics": {
            **metrics,
            "min_bets_required": LIVE_QUALITY_MIN_BETS,
            "samples_evaluated": samples,
            "min_samples_required": QUALITY_GATE_MIN_SAMPLES,
            "consecutive_passes": consecutive_passes,
            "consecutive_required": consecutive_required,
            "age_hours": round(age_h, 2) if age_h is not None else None,
        },
    }
