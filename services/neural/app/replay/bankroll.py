"""Paper bankroll for historical replay. It never writes the production ledger."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Optional, Tuple

from ..backtest.bankroll import BacktestBankroll


class ReplayBankroll(BacktestBankroll):
    """Fixed stake, with a reset to the initial balance after bankruptcy."""

    def __init__(self, initial_balance: float, stake: float, bankruptcy_threshold: float):
        config = SimpleNamespace(
            initial_bankroll=float(initial_balance),
            stake_strategy="FIXED",
            fixed_stake=float(stake),
            percent_stake=0.0,
            kelly_fraction=0.25,
            max_stake_cap=1.0,
            max_exposure=1.0,
            min_stake=min(float(stake), float(initial_balance)),
        )
        super().__init__(config)
        self.fixed_stake = float(stake)
        self.bankruptcy_threshold = float(bankruptcy_threshold)
        self.bankruptcies = 0
        self.min_balance = float(initial_balance)
        self.peak_equity = float(initial_balance)
        self.max_drawdown = 0.0

    def can_place_bet(self, stake: float, event_id: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        if stake <= 0:
            return False, "STAKE_TOO_LOW"
        if stake > self.current_balance + 1e-9:
            return False, "INSUFFICIENT_FUNDS"
        return True, None

    def ensure_solvent(self, now: datetime) -> bool:
        """Count a bankruptcy and restore the starting cash when a stake no longer fits."""
        self.release_due(now)
        self._mark_equity()
        if self.current_balance >= self.bankruptcy_threshold and self.current_balance >= self.fixed_stake:
            return False
        self.bankruptcies += 1
        self.current_balance = float(self.initial_balance)
        self._mark_equity()
        return True

    def note_equity(self) -> None:
        self._mark_equity()

    def _mark_equity(self) -> None:
        equity = float(self.current_balance + self.current_exposure)
        self.min_balance = min(self.min_balance, float(self.current_balance))
        self.peak_equity = max(self.peak_equity, equity)
        self.max_drawdown = max(self.max_drawdown, self.peak_equity - equity)
