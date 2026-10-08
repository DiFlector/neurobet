from datetime import datetime
from typing import List, Dict, Any, Optional, Literal, Tuple
from dataclasses import dataclass, field
from .config import BacktestConfig


@dataclass
class SimulatedBet:
    """Represents a virtual bet placed during backtesting."""
    bet_id: str
    event_id: str
    target_outcome: str  # "p1" or "p2"
    placed_at: datetime
    odds: float
    stake: float
    potential_payout: float
    model_probability: float
    edge: float
    status: Literal["PENDING", "WON", "LOST", "VOID"] = "PENDING"
    settled_at: Optional[datetime] = None
    payout: float = 0.0
    net_profit: float = 0.0


@dataclass
class EquityPoint:
    """Historical equity curve point."""
    timestamp: datetime
    balance: float
    exposure: float
    equity: float  # balance + exposure
    total_pnl: float


class BacktestBankroll:
    """
    Virtual bankroll manager for backtesting.
    Enforces risk limits, stake sizing (Fixed / Percent / Kelly), exposure caps,
    and maintains an immutable timeline of balance and equity changes.
    """

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.initial_balance = float(config.initial_bankroll)
        self.current_balance = float(config.initial_bankroll)
        self.current_exposure = 0.0
        self.peak_exposure = 0.0
        
        self.active_bets: Dict[str, SimulatedBet] = {}
        self.settled_bets: List[SimulatedBet] = []
        
        # Initial equity history
        self.equity_curve: List[EquityPoint] = [
            EquityPoint(
                timestamp=datetime(2020, 1, 1),
                balance=self.current_balance,
                exposure=0.0,
                equity=self.current_balance,
                total_pnl=0.0,
            )
        ]

    def calculate_stake(self, probability: float, odds: float) -> float:
        """
        Calculate recommended stake amount based on configured strategy.
        """
        if self.current_balance <= 0:
            return 0.0

        edge = (probability * odds) - 1.0
        if edge <= 0:
            return 0.0

        strategy = self.config.stake_strategy
        raw_stake = 0.0

        if strategy == "FIXED":
            raw_stake = self.config.fixed_stake
        elif strategy == "PERCENT":
            raw_stake = self.current_balance * self.config.percent_stake
        elif strategy == "KELLY":
            # Kelly formula: f = (p * b - q) / b, where b = odds - 1, q = 1 - p
            b = odds - 1.0
            p = probability
            q = 1.0 - p
            if b > 0:
                f_star = (p * b - q) / b
                f_star = max(0.0, f_star)
                f_fractional = f_star * self.config.kelly_fraction
                raw_stake = self.current_balance * f_fractional
            else:
                raw_stake = 0.0
        else:
            raw_stake = self.config.fixed_stake

        # Apply maximum stake cap (e.g. 5% of current balance)
        max_allowed_stake = self.current_balance * self.config.max_stake_cap
        stake = min(raw_stake, max_allowed_stake)

        # Enforce minimum stake threshold
        if stake < self.config.min_stake:
            return 0.0

        return round(stake, 2)

    def can_place_bet(self, stake: float) -> Tuple[bool, Optional[str]]:
        """
        Check if stake meets cash and portfolio exposure limits.
        """
        if stake <= 0:
            return False, "STAKE_TOO_LOW"

        # Check available cash
        if stake > self.current_balance:
            return False, "INSUFFICIENT_FUNDS"

        # Check max exposure limit (e.g. max 20% of total equity)
        total_equity = self.current_balance + self.current_exposure
        projected_exposure = self.current_exposure + stake
        max_allowed_exposure = total_equity * self.config.max_exposure

        if projected_exposure > max_allowed_exposure:
            return False, "EXPOSURE_LIMIT_BREACHED"

        return True, None

    def place_bet(
        self,
        bet_id: str,
        event_id: str,
        target_outcome: str,
        placed_at: datetime,
        odds: float,
        stake: float,
        probability: float,
        edge: float,
    ) -> Optional[SimulatedBet]:
        """
        Deduct stake from available balance and register active bet.
        """
        can_place, reason = self.can_place_bet(stake)
        if not can_place:
            return None

        # Lock stake into exposure
        self.current_balance -= stake
        self.current_exposure += stake
        if self.current_exposure > self.peak_exposure:
            self.peak_exposure = self.current_exposure

        bet = SimulatedBet(
            bet_id=bet_id,
            event_id=event_id,
            target_outcome=target_outcome,
            placed_at=placed_at,
            odds=odds,
            stake=stake,
            potential_payout=round(stake * odds, 2),
            model_probability=probability,
            edge=edge,
            status="PENDING",
        )
        self.active_bets[bet_id] = bet

        # Record equity point
        self.equity_curve.append(
            EquityPoint(
                timestamp=placed_at,
                balance=self.current_balance,
                exposure=self.current_exposure,
                equity=round(self.current_balance + self.current_exposure, 2),
                total_pnl=round((self.current_balance + self.current_exposure) - self.initial_balance, 2),
            )
        )
        return bet

    def settle_bet(
        self,
        bet_id: str,
        actual_winner: str,  # "p1" or "p2" or "void"
        settled_at: datetime,
    ) -> Optional[SimulatedBet]:
        """
        Settle an active bet, calculate payout/PnL, release exposure, and record ledger update.
        """
        bet = self.active_bets.pop(bet_id, None)
        if not bet:
            return None

        # Release exposure
        self.current_exposure -= bet.stake

        if actual_winner == "void":
            bet.status = "VOID"
            bet.payout = bet.stake
            bet.net_profit = 0.0
            self.current_balance += bet.stake
        elif actual_winner == bet.target_outcome:
            bet.status = "WON"
            bet.payout = round(bet.stake * bet.odds, 2)
            bet.net_profit = round(bet.payout - bet.stake, 2)
            self.current_balance += bet.payout
        else:
            bet.status = "LOST"
            bet.payout = 0.0
            bet.net_profit = -bet.stake

        bet.settled_at = settled_at
        self.settled_bets.append(bet)

        # Record equity point
        self.equity_curve.append(
            EquityPoint(
                timestamp=settled_at,
                balance=round(self.current_balance, 2),
                exposure=round(self.current_exposure, 2),
                equity=round(self.current_balance + self.current_exposure, 2),
                total_pnl=round((self.current_balance + self.current_exposure) - self.initial_balance, 2),
            )
        )
        return bet
