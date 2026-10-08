"""Configuration settings for Bet Manager service."""

import os
from dataclasses import dataclass


@dataclass
class BetManagerConfig:
    max_odds_staleness_sec: float = float(os.getenv("MAX_ODDS_STALENESS_SEC", "15.0"))
    max_state_staleness_sec: float = float(os.getenv("MAX_STATE_STALENESS_SEC", "30.0"))
    min_edge: float = float(os.getenv("MIN_EDGE", "0.03"))
    min_confidence: float = float(os.getenv("MIN_CONFIDENCE", "0.50"))
    min_odds: float = float(os.getenv("MIN_ODDS", "1.05"))
    max_odds: float = float(os.getenv("MAX_ODDS", "50.0"))
    min_stake: float = float(os.getenv("MIN_STAKE", "100.0"))
    max_stake: float = float(os.getenv("MAX_STAKE", "10000.0"))
    max_exposure_fraction: float = float(os.getenv("MAX_EXPOSURE_FRACTION", "0.20"))
    daily_loss_limit_fraction: float = float(os.getenv("DAILY_LOSS_LIMIT_FRACTION", "0.15"))
    allowed_slippage_pct: float = float(os.getenv("ALLOWED_SLIPPAGE_PCT", "0.05"))
    default_account_name: str = os.getenv("DEFAULT_ACCOUNT_NAME", "default_paper_account")
    initial_bankroll: float = float(os.getenv("INITIAL_BANKROLL", "100000.00"))
    currency: str = os.getenv("CURRENCY", "RUB")


config = BetManagerConfig()
