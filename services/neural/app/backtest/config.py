from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


class BacktestConfig(BaseModel):
    """
    Configuration for realistic walk-forward backtesting simulation.
    """
    sport_code: str = Field(default="tennis", description="Target sport code")
    market: str = Field(default="match_winner", description="Market name")
    experiment_name: Optional[str] = Field(default=None, description="Optional custom name for experiment registry")
    period_start: Optional[str] = Field(default=None, description="Evaluation period start timestamp ISO")
    period_end: Optional[str] = Field(default=None, description="Evaluation period end timestamp ISO")
    use_llm: bool = Field(default=False, description="Whether LLM reasoning is enabled in this experiment")

    
    # Financial & Bankroll parameters
    initial_bankroll: float = Field(default=100_000.0, gt=0.0, description="Starting virtual bankroll in RUB")
    stake_strategy: Literal["FIXED", "PERCENT", "KELLY"] = Field(
        default="KELLY",
        description="Staking method",
    )
    fixed_stake: float = Field(default=1_000.0, gt=0.0, description="Fixed stake amount in RUB")
    percent_stake: float = Field(default=0.02, gt=0.0, le=0.2, description="Stake as percentage of current bankroll")
    kelly_fraction: float = Field(default=0.25, gt=0.0, le=1.0, description="Fractional Kelly multiplier (e.g. 0.25 for Quarter Kelly)")
    
    # Risk limits
    max_stake_cap: float = Field(default=0.05, gt=0.0, le=0.5, description="Max stake cap as fraction of bankroll (e.g. 5%)")
    max_exposure: float = Field(default=0.20, gt=0.0, le=1.0, description="Max cumulative active un-settled exposure (e.g. 20%)")
    min_stake: float = Field(default=100.0, gt=0.0, description="Minimum allowed stake amount in RUB")
    
    # Strategy hurdle parameters
    min_edge: float = Field(default=0.03, ge=0.0, description="Minimum required edge (3%) to consider betting")
    min_probability: float = Field(default=0.40, ge=0.0, le=1.0, description="Minimum model probability")
    max_odds: float = Field(default=10.0, gt=1.0, description="Maximum bookmaker odds to bet on")
    
    # Execution realism parameters
    execution_latency_sec: float = Field(default=2.0, ge=0.0, description="Simulated execution latency in seconds")
    max_odds_staleness_sec: float = Field(default=15.0, gt=0.0, description="Max allowed staleness of odds snapshot before rejecting")
    allow_slippage: bool = Field(default=True, description="Whether odds movement during latency period affects execution")
    
    # Temporal & Walk-forward parameters
    n_splits: int = Field(default=4, ge=2, description="Number of chronological walk-forward splits")
    split_mode: Literal["EXPANDING", "ROLLING"] = Field(default="EXPANDING", description="Walk-forward window mode")
    purge_window_sec: float = Field(default=300.0, ge=0.0, description="Purge/embargo time gap between train and test folds (seconds)")
    
    random_seed: int = Field(default=42, description="Deterministic random seed")
