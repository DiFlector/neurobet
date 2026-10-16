"""Tennis stake windows. The numbers come from sports/tennis/tennis.cfg."""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence

from tennis_adapter.policy import load_tennis_policy, windows_allow

_POLICY = load_tennis_policy()
MIN_STAKE_PROBABILITY = _POLICY.min_probability
SHORT_ODDS_LO = _POLICY.short_odds_lo
SHORT_ODDS_HI = _POLICY.short_odds_hi
MIN_STAKE_ODDS = _POLICY.mid_odds_lo
SHARED_ODDS_HI = _POLICY.mid_odds_hi
MAX_STAKE_ODDS = _POLICY.match_odds_hi
EDGE_GRID = (0.02, 0.03, 0.05, 0.07)
ODDS_BANDS = ((1.30, 1.399), (1.60, 1.799), (1.80, 2.399))
CONFIRMATIONS = (2, 3)
INTERVALS_SEC = (0.0, 60.0)
PHASES = ("any", "set_1", "set_2", "set_3")
DISAGREEMENT_CAPS = (0.05, 0.10, 1.0)
MIN_VALIDATION_BETS = 30


def stake_gate(probability: float, odds: float, market: str = "match_winner", sport_code: str = "tennis") -> tuple:
    """Tennis registration window. Another sport is not cut by these numbers."""
    if str(sport_code or "").strip().lower() != "tennis":
        return True, ""
    return windows_allow(probability, odds, market)


def disagreement(probabilities: Sequence[float]) -> float:
    """Spread between model probabilities of the same side. One model has no spread."""
    values = [float(item) for item in probabilities]
    if len(values) < 2:
        return 0.0
    return max(values) - min(values)


def _median(values: Sequence[float]) -> float:
    ordered = sorted(float(item) for item in values)
    if not ordered:
        return 0.0
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def certainty(probabilities: Sequence[float], reliability: float = 1.0) -> float:
    """Agreement of the available probabilities, scaled by calibration-segment reliability."""
    spread = disagreement(probabilities)
    scale = min(1.0, max(0.0, float(reliability)))
    return round(max(0.0, min(1.0, (1.0 - spread) * scale)), 4)


def accepts(row: Dict, policy: Dict) -> bool:
    odds = float(row.get("odds") or 0.0)
    phase = str(row.get("phase") or "any")
    wanted = str(policy.get("phase") or "any")
    return (
        float(row.get("edge") or 0.0) >= float(policy["min_edge"])
        and float(policy["odds_lo"]) <= odds <= float(policy["odds_hi"])
        and float(row.get("disagreement") or 0.0) <= float(policy["max_disagreement"])
        and int(row.get("confirmations") or 0) >= int(policy["confirmations"])
        and float(row.get("interval_sec") or 0.0) >= float(policy.get("min_interval_sec") or 0.0)
        and (wanted == "any" or phase == wanted)
    )


def select_policy(rows: Iterable[Dict]) -> Dict:
    """Pick the validation rule with a positive median CLV. Otherwise the rule is no bet."""
    sample = list(rows)
    best = None
    for edge in EDGE_GRID:
        for low, high in ODDS_BANDS:
            for confirms in CONFIRMATIONS:
                for interval in INTERVALS_SEC:
                    for phase in PHASES:
                        for cap in DISAGREEMENT_CAPS:
                            policy = {
                                "min_edge": edge,
                                "odds_lo": low,
                                "odds_hi": high,
                                "confirmations": confirms,
                                "min_interval_sec": interval,
                                "phase": phase,
                                "max_disagreement": cap,
                            }
                            chosen = [row for row in sample if accepts(row, policy) and row.get("clv") is not None]
                            if len(chosen) < MIN_VALIDATION_BETS:
                                continue
                            median_clv = _median([row["clv"] for row in chosen])
                            if best is None or median_clv > best["median_clv"]:
                                best = {**policy, "median_clv": median_clv, "bets": len(chosen)}
    if best is None or best["median_clv"] <= 0:
        return {"action": "no_bet", "reason": "validation_clv_not_positive", "bets": 0}
    best["action"] = "bet"
    return best


def _kelly_fraction(probability: float, odds: float, fraction: float) -> float:
    net = odds - 1.0
    if net <= 0:
        return 0.0
    full = ((probability * net) - (1.0 - probability)) / net
    return max(0.0, full) * fraction


def compare_stake_sizes(decisions: Sequence[Dict], bankroll: float = 100_000.0) -> Dict[str, Dict[str, float]]:
    """Size one frozen list of decisions. The list itself does not change between modes."""
    modes = {
        "fixed": lambda balance, probability, odds: 100.0,
        "percent_0.25": lambda balance, probability, odds: balance * 0.0025,
        "percent_0.5": lambda balance, probability, odds: balance * 0.005,
        "kelly_1_8": lambda balance, probability, odds: balance * _kelly_fraction(probability, odds, 0.125),
        "kelly_1_4": lambda balance, probability, odds: balance * _kelly_fraction(probability, odds, 0.25),
    }
    report = {}
    for name, rule in modes.items():
        balance = float(bankroll)
        peak = balance
        max_drawdown = 0.0
        profit = 0.0
        turnover = 0.0
        placed = 0
        for row in decisions:
            stake = min(rule(balance, float(row["probability"]), float(row["odds"])), balance * 0.05, balance)
            if stake <= 0:
                continue
            placed += 1
            turnover += stake
            change = stake * (float(row["odds"]) - 1.0) if row.get("won") else -stake
            balance += change
            profit += change
            peak = max(peak, balance)
            max_drawdown = max(max_drawdown, peak - balance)
        report[name] = {
            "bets": float(placed),
            "roi": (profit / turnover) if turnover else 0.0,
            "max_drawdown": max_drawdown,
            "final_bankroll": balance,
        }
    return report


def prefer_stake_mode(report: Dict[str, Dict[str, float]]) -> str:
    """Prefer the mode with the smaller drawdown when it still places the same decisions."""
    ranked = sorted(report, key=lambda name: (report[name]["max_drawdown"], -report[name]["final_bankroll"]))
    return ranked[0] if ranked else "fixed"


def promotion_allowed(
    shadow_days: float,
    paired_observations: int,
    psi: float,
    counterfactual_roi: float,
    brier_delta: float,
) -> tuple:
    """The same gates as model release. A failed gate blocks promotion."""
    reasons: List[str] = []
    if shadow_days < 7:
        reasons.append("shadow_days")
    if paired_observations < 100:
        reasons.append("paired_observations")
    if psi > 0.25:
        reasons.append("psi")
    if counterfactual_roi < 0:
        reasons.append("counterfactual_roi")
    if brier_delta > 0.02:
        reasons.append("brier_delta")
    return (not reasons, reasons)
