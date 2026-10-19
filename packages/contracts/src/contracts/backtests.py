"""Historical replay of registered model checkpoints.

This is not the walk-forward trainer. A run scores saved artifacts on finished
matches and keeps its bankroll outside the production ledger.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, field_validator, model_validator

from .base import BaseContract, utc_now


class BacktestReplayConfig(BaseContract):
    """Gates and paper bankroll used by one historical replay."""

    market: str = Field(default="match_winner", description="Kept for older clients. markets is the list that is actually replayed.")
    markets: List[Literal["match_winner", "set_winner"]] = Field(
        default_factory=lambda: ["match_winner", "set_winner"],
        description="Markets to score. Tennis v2 and the composite model can score sets; other checkpoints ignore set_winner.",
    )
    match_decision_mode: Literal["set_confirmed", "independent"] = Field(
        default="set_confirmed",
        description="Tennis replay only: match head waits for confirmed set sides, or selects independently from set 2.",
    )
    match_limit: int = Field(default=100, ge=1, le=2000, description="How many latest finished matches to replay")
    event_ids: Optional[List[str]] = Field(
        default=None,
        description="Replay these event UUIDs. When set, match_limit is not used to choose matches.",
    )
    min_edge: Optional[float] = Field(default=None, ge=0.0, description="Common edge override; omitted values inherit tennis.cfg")
    min_probability: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Common probability override; omitted values inherit tennis.cfg")
    odds_gate_mode: Optional[Literal["live", "range"]] = Field(
        default=None,
        description="live uses the same odds windows as inference; range uses min_odds and max_odds",
    )
    min_odds: Optional[float] = Field(default=None, gt=1.0, description="Common lower odds override in range mode")
    max_odds: Optional[float] = Field(default=None, gt=1.0, description="Common upper odds override in range mode")
    short_odds_lo: Optional[float] = Field(default=None, gt=1.0, description="Tennis short-price window start, inclusive")
    short_odds_hi: Optional[float] = Field(default=None, gt=1.0, description="Tennis short-price window end, exclusive")
    mid_odds_lo: Optional[float] = Field(default=None, gt=1.0, description="Tennis middle-price window start, inclusive")
    mid_odds_hi: Optional[float] = Field(default=None, gt=1.0, description="Tennis middle-price window end, exclusive")
    match_odds_hi: Optional[float] = Field(default=None, gt=1.0, description="Tennis match window end, exclusive")
    observe_set: Optional[int] = Field(default=None, ge=1, le=5, description="Tennis set number that is watched and not staked")
    stake_sets: Optional[List[int]] = Field(
        default=None,
        description="When set, only these tennis set numbers are staked. Other sets are skipped.",
    )
    confirmations: Optional[int] = Field(default=None, ge=2, le=5, description="Common confirmation override; omitted inherits tennis.cfg")
    match_min_probability: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    set_min_probability: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    match_min_edge: Optional[float] = Field(default=None, ge=0.0)
    set_min_edge: Optional[float] = Field(default=None, ge=0.0)
    match_confirmations: Optional[int] = Field(default=None, ge=2, le=5)
    set_confirmations: Optional[int] = Field(default=None, ge=2, le=5)
    match_gate_mode: Optional[Literal["live", "range"]] = None
    set_gate_mode: Optional[Literal["live", "range"]] = None
    match_min_odds: Optional[float] = Field(default=None, gt=1.0)
    match_max_odds: Optional[float] = Field(default=None, gt=1.0)
    set_min_odds: Optional[float] = Field(default=None, gt=1.0)
    set_max_odds: Optional[float] = Field(default=None, gt=1.0)
    max_odds_staleness_sec: float = Field(default=15.0, gt=0.0, description="Reject a fill when the line is older than this")
    execution_latency_sec: float = Field(default=2.0, ge=0.0, description="Delay between the decision and the fill")
    allow_slippage: bool = Field(default=True, description="Reject the fill when latency removes the required edge")
    initial_bankroll: float = Field(default=5000.0, gt=0.0, description="Paper balance at the start and after each bankruptcy")
    fixed_stake: float = Field(default=100.0, gt=0.0, description="Stake placed on every accepted side")
    bankruptcy_threshold: float = Field(
        default=100.0,
        gt=0.0,
        description="When available balance falls below this value, count a bankruptcy and restore the initial balance",
    )

    @field_validator("stake_sets")
    @classmethod
    def stake_sets_are_set_numbers(cls, value: Optional[List[int]]) -> Optional[List[int]]:
        if not value:
            return None
        cleaned = []
        for item in value:
            number = int(item)
            if number < 1 or number > 5:
                raise ValueError("stake_sets must be set numbers from 1 to 5")
            if number not in cleaned:
                cleaned.append(number)
        return cleaned

    @field_validator("event_ids")
    @classmethod
    def event_ids_are_unique_uuids(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if not value:
            return value
        import uuid

        cleaned = []
        for item in value:
            try:
                cleaned.append(str(uuid.UUID(str(item))))
            except ValueError as exc:
                raise ValueError("event_ids must contain UUIDs") from exc
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("event_ids must be unique")
        return cleaned

    @model_validator(mode="after")
    def bounds(self) -> "BacktestReplayConfig":
        if self.min_odds is not None and self.max_odds is not None and self.max_odds <= self.min_odds:
            raise ValueError("max_odds must be greater than min_odds")
        if self.fixed_stake > self.initial_bankroll:
            raise ValueError("fixed_stake must fit into the initial bankroll")
        if not self.markets:
            raise ValueError("at least one market is required")
        return self


class BacktestModelSelector(BaseContract):
    """One registered checkpoint, or every ACTIVE and SHADOW checkpoint of a sport."""

    scope: Literal["model", "sport"] = Field(default="model")
    sport_code: str = Field(default="tennis")
    model_version_id: Optional[str] = Field(default=None, description="Required when scope is model")

    @model_validator(mode="after")
    def model_id_required(self) -> "BacktestModelSelector":
        if self.scope == "model" and not self.model_version_id:
            raise ValueError("model_version_id is required when scope is model")
        return self


class BacktestRunRequest(BaseContract):
    """Body of POST /api/backtests/run."""

    selector: BacktestModelSelector
    config: BacktestReplayConfig = Field(default_factory=BacktestReplayConfig)


class StrategySearchRequest(BaseContract):
    """Search tennis stake gates on one fixed window. The final window is not used."""

    model_version_id: str
    sport_code: str = "tennis"
    search_event_ids: List[str] = Field(default_factory=list)
    final_event_ids: List[str] = Field(default_factory=list)
    model_final_event_ids: List[str] = Field(default_factory=list)

    @field_validator("model_version_id")
    @classmethod
    def model_uuid(cls, value: str) -> str:
        import uuid

        try:
            return str(uuid.UUID(str(value)))
        except ValueError as exc:
            raise ValueError("model_version_id must be a UUID") from exc

    @field_validator("search_event_ids", "final_event_ids", "model_final_event_ids")
    @classmethod
    def unique_uuids(cls, value: List[str]) -> List[str]:
        import uuid

        cleaned = []
        for item in value:
            try:
                cleaned.append(str(uuid.UUID(str(item))))
            except ValueError as exc:
                raise ValueError("event ids must be UUIDs") from exc
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("event ids must be unique")
        return cleaned

    @model_validator(mode="after")
    def windows_do_not_overlap(self) -> "StrategySearchRequest":
        groups = [set(self.search_event_ids), set(self.final_event_ids), set(self.model_final_event_ids)]
        for left, right in ((0, 1), (0, 2), (1, 2)):
            if groups[left] & groups[right]:
                raise ValueError("strategy windows must not overlap")
        return self


class BacktestBetRecord(BaseContract):
    """One paper stake inside a replay. It is not a production bet."""

    event_id: str
    market: str
    outcome: str
    placed_at: datetime
    odds: float
    stake: float
    probability: float
    edge: float
    status: Literal["PENDING", "WON", "LOST", "VOID"]
    payout: float = 0.0
    net_profit: float = 0.0


class BacktestModelResult(BaseContract):
    """Paper result of one registered model."""

    model_version_id: str
    model_name: str
    version_tag: str
    sport_code: str
    status: str
    matches_seen: int = 0
    bets: int = 0
    wins: int = 0
    losses: int = 0
    voids: int = 0
    win_rate: Optional[float] = None
    turnover: float = 0.0
    net_pnl: float = 0.0
    roi: Optional[float] = None
    initial_bankroll: float
    final_balance: float
    min_balance: float
    max_drawdown: float = 0.0
    bankruptcies: int = 0
    rejection_counts: Dict[str, int] = Field(default_factory=dict)
    gates_applied: List[str] = Field(default_factory=list)
    bet_records: List[BacktestBetRecord] = Field(default_factory=list)


class BacktestRunStatus(BaseContract):
    """Queued, running, completed, or failed historical replay."""

    id: str
    status: Literal["queued", "running", "completed", "failed"]
    sport_code: str
    scope: Literal["model", "sport"]
    request: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    results: List[BacktestModelResult] = Field(default_factory=list)
