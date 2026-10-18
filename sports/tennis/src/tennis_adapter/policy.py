"""Numeric tennis stake limits. The rules that use these numbers live in the caller."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Union


@dataclass(frozen=True)
class TennisPolicy:
    min_probability: float = 0.40
    short_odds_lo: float = 1.30
    short_odds_hi: float = 1.40
    mid_odds_lo: float = 1.60
    mid_odds_hi: float = 1.80
    match_odds_hi: float = 2.40
    min_edge: float = 0.02
    confirmations: int = 2
    observe_set: int = 1
    stake_sets: str = "2"
    match_model: str = "tennis_hist_gradient_roi tennis_v3.1.0"
    set_model: str = "tennis_gradient_boosting_epoch tennis_v3.0.0"
    match_gate_mode: str = "live"
    set_gate_mode: str = "live"
    match_min_odds: float = 1.30
    match_max_odds: float = 2.40
    set_min_odds: float = 1.30
    set_max_odds: float = 2.40
    match_min_probability: float = 0.40
    set_min_probability: float = 0.40
    match_min_edge: float = 0.02
    set_min_edge: float = 0.02
    match_confirmations: int = 2
    set_confirmations: int = 2
    kelly_fraction: float = 0.25
    max_stake_fraction: float = 0.05
    min_stake: float = 50.0
    min_data_quality: float = 50.0
    min_research_quality: float = 0.0
    max_context_age_seconds: int = 180

    def as_dict(self) -> dict:
        return {
            "min_probability": self.min_probability,
            "short_odds_lo": self.short_odds_lo,
            "short_odds_hi": self.short_odds_hi,
            "mid_odds_lo": self.mid_odds_lo,
            "mid_odds_hi": self.mid_odds_hi,
            "match_odds_hi": self.match_odds_hi,
            "min_edge": self.min_edge,
            "confirmations": self.confirmations,
            "observe_set": self.observe_set,
            "stake_sets": self.stake_sets,
            "match_model": self.match_model,
            "set_model": self.set_model,
            "match_gate_mode": self.match_gate_mode,
            "set_gate_mode": self.set_gate_mode,
            "match_min_odds": self.match_min_odds,
            "match_max_odds": self.match_max_odds,
            "set_min_odds": self.set_min_odds,
            "set_max_odds": self.set_max_odds,
            "match_min_probability": self.match_min_probability,
            "set_min_probability": self.set_min_probability,
            "match_min_edge": self.match_min_edge,
            "set_min_edge": self.set_min_edge,
            "match_confirmations": self.match_confirmations,
            "set_confirmations": self.set_confirmations,
            "kelly_fraction": self.kelly_fraction,
            "max_stake_fraction": self.max_stake_fraction,
            "min_stake": self.min_stake,
            "min_data_quality": self.min_data_quality,
            "min_research_quality": self.min_research_quality,
            "max_context_age_seconds": self.max_context_age_seconds,
        }


def _candidate_paths() -> list:
    env = os.environ.get("TENNIS_POLICY_PATH")
    here = Path(__file__).resolve()
    return [
        Path(env) if env else None,
        Path("/app/sports/tennis/tennis.cfg"),
        here.parents[2] / "tennis.cfg",
        here.with_name("tennis.cfg"),
    ]


def load_tennis_policy() -> TennisPolicy:
    """Read sports/tennis/tennis.cfg. Missing file keeps the built-in numbers."""
    values = TennisPolicy().as_dict()
    for path in _candidate_paths():
        if path is None or not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            if key not in values:
                continue
            current = values[key]
            if isinstance(current, str):
                values[key] = value
            else:
                values[key] = int(float(value)) if isinstance(current, int) else float(value)
        break
    return TennisPolicy(**values)


def policy_from_mapping(data: Optional[Mapping]) -> TennisPolicy:
    """A backtest request may replace the file numbers for that run only."""
    base = load_tennis_policy().as_dict()
    if data:
        for key in base:
            if key in data and data[key] is not None:
                current = base[key]
                if isinstance(current, str):
                    continue
                base[key] = int(float(data[key])) if isinstance(current, int) else float(data[key])
    return TennisPolicy(**base)


def _is_set_market(market: str) -> bool:
    text = str(market or "")
    return text == "set_winner" or text.startswith("set_winner:")


def market_limits(market: str, policy: Optional[TennisPolicy] = None) -> dict:
    """Numbers for one market. A zero confirmation count keeps the shared value."""
    limits = policy or load_tennis_policy()
    if _is_set_market(market):
        return {
            "mode": str(limits.set_gate_mode or "live"),
            "min_odds": float(limits.set_min_odds),
            "max_odds": float(limits.set_max_odds),
            "min_probability": float(limits.set_min_probability),
            "min_edge": float(limits.set_min_edge),
            "confirmations": int(limits.set_confirmations or limits.confirmations),
        }
    return {
        "mode": str(limits.match_gate_mode or "live"),
        "min_odds": float(limits.match_min_odds),
        "max_odds": float(limits.match_max_odds),
        "min_probability": float(limits.match_min_probability),
        "min_edge": float(limits.match_min_edge),
        "confirmations": int(limits.match_confirmations or limits.confirmations),
    }


def windows_allow(probability: float, odds: float, market: str, policy: Optional[TennisPolicy] = None) -> tuple:
    """Tennis price windows. Upper bounds are excluded."""
    limits = policy or load_tennis_policy()
    chosen = market_limits(market, limits)
    if float(probability) < chosen["min_probability"]:
        return False, "LOW_PROBABILITY"
    price = float(odds)
    if chosen["mode"] == "range":
        if price < chosen["min_odds"]:
            return False, "ODDS_TOO_SHORT"
        if price >= chosen["max_odds"]:
            return False, "ODDS_TOO_LONG"
        return True, ""
    if limits.short_odds_lo <= price < limits.short_odds_hi or limits.mid_odds_lo <= price < limits.mid_odds_hi:
        return True, ""
    if not _is_set_market(market) and limits.mid_odds_hi <= price < limits.match_odds_hi:
        return True, ""
    if price < limits.short_odds_lo:
        return False, "ODDS_TOO_SHORT"
    if price >= limits.match_odds_hi:
        return False, "ODDS_TOO_LONG"
    return False, "ODDS_OUT_OF_WINDOW"


def _assigned(text: str) -> tuple:
    parts = str(text or "").split()
    if len(parts) < 2:
        return "", ""
    return parts[0], parts[1]


def model_commits_market(model_name: str, version_tag: str, market: str) -> bool:
    """The named match line registers only the match. The named set line registers only sets."""
    policy = load_tennis_policy()
    name = str(model_name or "")
    text = str(market or "")
    match_name = _assigned(policy.match_model)[0]
    set_name = _assigned(policy.set_model)[0]
    if text == "match_winner":
        if name and name == set_name:
            return False
        return True
    if text == "set_winner" or text.startswith("set_winner:"):
        if name and name == match_name:
            return False
        return True
    return True


def set_number(market: Union[str, int]) -> Optional[int]:
    if isinstance(market, int):
        return market
    text = str(market or "")
    if not text.startswith("set_winner:"):
        return None
    try:
        return int(text.split(":", 1)[1])
    except (TypeError, ValueError):
        return None


def set_is_observation_only(market: Union[str, int], sport_code: str, observe_set: Optional[int] = None) -> bool:
    """The configured tennis set is watched and is not registered as a stake."""
    if str(sport_code or "").strip().lower() != "tennis":
        return False
    number = set_number(market)
    if number is None:
        return False
    target = load_tennis_policy().observe_set if observe_set is None else int(observe_set)
    return number == target


def stake_set_numbers(policy: Optional[TennisPolicy] = None) -> Optional[set]:
    """Configured stake sets. An empty value leaves every non-observed set open."""
    text = str((policy or load_tennis_policy()).stake_sets or "").strip().lower()
    if not text or text == "all":
        return None
    numbers = set()
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        numbers.add(int(float(part)))
    return numbers or None


def set_is_stake_allowed(market: Union[str, int], sport_code: str, policy: Optional[TennisPolicy] = None) -> bool:
    """True when this tennis set may be journaled. Other sports are unchanged."""
    if str(sport_code or "").strip().lower() != "tennis":
        return True
    number = set_number(market)
    if number is None:
        return True
    allowed = stake_set_numbers(policy)
    if allowed is None:
        return True
    return number in allowed
