"""Stake size for a tennis side. The numbers come from tennis.cfg."""

from __future__ import annotations

from typing import Optional

from .policy import TennisPolicy, load_tennis_policy


def expected_value(probability: float, odds: float) -> float:
    """Return p * odds - 1. A non-positive value is not a stake."""
    return float(probability) * float(odds) - 1.0


def probability_edge(probability: float, odds: float) -> float:
    """Return the gap between the model and the implied price."""
    price = float(odds)
    if price <= 1.0:
        return -1.0
    return float(probability) - (1.0 / price)


def fractional_kelly(probability: float, odds: float, kelly_fraction: float) -> float:
    """Return a capped Kelly share of the bankroll. Negative edge is zero."""
    price = float(odds)
    if price <= 1.0:
        return 0.0
    full = (float(probability) * price - 1.0) / (price - 1.0)
    if full <= 0.0:
        return 0.0
    return full * max(0.0, float(kelly_fraction))


def quote_stake(
    *,
    balance: float,
    odds: float,
    probability: float,
    data_quality: Optional[float] = None,
    research_quality: Optional[float] = None,
    llm_confidence: Optional[float] = None,
    llm_verdict: Optional[str] = None,
    injury_risk: float = 0.0,
    fatigue_risk: float = 0.0,
    news_risk: float = 0.0,
    contradictions: int = 0,
    exposure_room: Optional[float] = None,
    policy: Optional[TennisPolicy] = None,
) -> dict:
    """Size one virtual stake. A blocked quote has stake 0 and a reason code."""
    limits = policy or load_tennis_policy()
    price = float(odds)
    chance = float(probability)
    ev = expected_value(chance, price)
    bank = max(0.0, float(balance))
    quote = {
        "expected_value": round(ev, 4),
        "probability_edge": round(probability_edge(chance, price), 4),
        "kelly_fraction": 0.0,
        "stake": 0.0,
        "potential_win": 0.0,
        "max_loss": 0.0,
        "reason": "",
    }
    verdict = str(llm_verdict or "").upper()
    if verdict == "NO_BET":
        quote["reason"] = "LLM_NO_BET"
        return quote
    if contradictions > 0:
        quote["reason"] = "LLM_CONTRADICTION"
        return quote
    if max(float(injury_risk), float(fatigue_risk), float(news_risk)) >= 0.7:
        quote["reason"] = "CONTEXT_RISK"
        return quote
    if ev <= 0.0:
        quote["reason"] = "NEGATIVE_EV"
        return quote
    if data_quality is not None and float(data_quality) < float(limits.min_data_quality):
        quote["reason"] = "QUALITY_TOO_LOW"
        return quote
    if research_quality is not None and float(research_quality) < float(limits.min_research_quality):
        quote["reason"] = "RESEARCH_QUALITY_TOO_LOW"
        return quote

    share = fractional_kelly(chance, price, limits.kelly_fraction)
    share = min(share, float(limits.max_stake_fraction))
    factor = 1.0
    if data_quality is not None:
        factor *= min(1.0, max(0.0, float(data_quality)) / 100.0)
    if research_quality is not None:
        factor *= 0.5 + 0.5 * min(1.0, max(0.0, float(research_quality)))
    if llm_confidence is not None:
        factor *= max(0.25, min(1.0, float(llm_confidence)))
    raw = round(bank * share, 2)
    if exposure_room is not None:
        raw = min(raw, round(max(0.0, float(exposure_room)), 2))
    raw = min(raw, round(bank, 2))
    stake = round(raw * factor, 2)
    # A neutral research score must not erase a stake that already fits the cap and the minimum.
    if raw >= float(limits.min_stake) and 0.0 < stake < float(limits.min_stake):
        stake = float(limits.min_stake)
    if stake < float(limits.min_stake):
        quote["kelly_fraction"] = round(share, 4)
        quote["reason"] = "STAKE_TOO_LOW"
        return quote
    quote.update(
        {
            "kelly_fraction": round(share, 4),
            "stake": stake,
            "potential_win": round(stake * (price - 1.0), 2),
            "max_loss": stake,
            "reason": "",
        }
    )
    return quote
