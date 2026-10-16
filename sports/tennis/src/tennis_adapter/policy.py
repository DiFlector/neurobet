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
    match_model: str = "tennis_logistic_regression tennis_v2.3.0"
    set_model: str = "tennis_composite tennis_v1.0.0"

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
            "match_model": self.match_model,
            "set_model": self.set_model,
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


def windows_allow(probability: float, odds: float, market: str, policy: Optional[TennisPolicy] = None) -> tuple:
    """Tennis price windows. Upper bounds are excluded."""
    limits = policy or load_tennis_policy()
    if float(probability) < limits.min_probability:
        return False, "LOW_PROBABILITY"
    price = float(odds)
    if limits.short_odds_lo <= price < limits.short_odds_hi or limits.mid_odds_lo <= price < limits.mid_odds_hi:
        return True, ""
    if market == "match_winner" and limits.mid_odds_hi <= price < limits.match_odds_hi:
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
    identity = (str(model_name or ""), str(version_tag or ""))
    text = str(market or "")
    if text == "match_winner":
        own, other = _assigned(policy.match_model), _assigned(policy.set_model)
    elif text == "set_winner" or text.startswith("set_winner:"):
        own, other = _assigned(policy.set_model), _assigned(policy.match_model)
    else:
        return True
    if identity == own:
        return True
    if identity == other:
        return False
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
