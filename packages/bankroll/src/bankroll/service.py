import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any, List

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from db.models.betting import VirtualAccount, LedgerEntry, Bet, BetSettlement
from .exceptions import (
    InsufficientFundsError,
    AccountNotFoundError,
    BetNotFoundError,
    InvalidSettlementError,
)

logger = logging.getLogger("neurobet.bankroll")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BankrollService:
    """
    Transactional core for virtual bankroll management and immutable financial ledger.
    Every balance change MUST be accompanied by an append-only LedgerEntry.
    """

    @staticmethod
    def get_or_create_account(
        session: Session,
        name: str = "default_paper_account",
        initial_balance: float = 100_000.00,
        currency: str = "RUB",
    ) -> VirtualAccount:
        """
        Retrieve or atomically create a virtual account with initial ledger deposit.
        """
        stmt = select(VirtualAccount).where(VirtualAccount.name == name)
        account = session.execute(stmt).scalar_one_or_none()

        if account:
            return account

        logger.info(f"Creating new VirtualAccount '{name}' with initial balance {initial_balance:.2f} {currency}")
        account = VirtualAccount(
            name=name,
            currency=currency,
            initial_balance=initial_balance,
            balance=initial_balance,
            locked_exposure=0.00,
            is_active=True,
        )
        session.add(account)
        session.flush()

        # Create first immutable transaction: INITIAL_DEPOSIT
        entry = LedgerEntry(
            account_id=account.id,
            bet_id=None,
            entry_type="INITIAL_DEPOSIT",
            amount=initial_balance,
            balance_after=initial_balance,
            description=f"Initial virtual deposit of {initial_balance:.2f} {currency}",
            created_at=utc_now(),
        )
        session.add(entry)
        session.flush()

        return account

    @staticmethod
    def get_account_summary(session: Session, account_id: uuid.UUID) -> Dict[str, Any]:
        """
        Get balance, exposure, equity, and derived metrics for account.
        """
        account = session.get(VirtualAccount, account_id)
        if not account:
            raise AccountNotFoundError(f"Account with id {account_id} not found.")

        # Recalculate ledger sum to verify balance integrity
        ledger_sum = session.query(func.coalesce(func.sum(LedgerEntry.amount), 0.0)).filter(
            LedgerEntry.account_id == account.id,
            LedgerEntry.entry_type != "INITIAL_DEPOSIT",
        ).scalar() or 0.0

        derived_balance = float(account.initial_balance) + float(ledger_sum)
        available_balance = float(account.balance)
        locked_exposure = float(account.locked_exposure)
        total_equity = available_balance + locked_exposure
        total_pnl = total_equity - float(account.initial_balance)

        return {
            "account_id": str(account.id),
            "name": account.name,
            "currency": account.currency,
            "initial_balance": float(account.initial_balance),
            "available_balance": round(available_balance, 2),
            "locked_exposure": round(locked_exposure, 2),
            "total_equity": round(total_equity, 2),
            "total_pnl": round(total_pnl, 2),
            "derived_balance": round(derived_balance, 2),
            "is_balanced": abs(available_balance - derived_balance) < 0.01,
        }

    @staticmethod
    def place_bet(
        session: Session,
        account_id: uuid.UUID,
        proposal_id: uuid.UUID,
        event_id: uuid.UUID,
        sport_code: str,
        market: str,
        outcome: str,
        odds: float,
        stake: float,
    ) -> Bet:
        """
        Atomically lock stake, deduct from available balance, and record BET_PLACED in ledger.
        """
        if stake <= 0:
            raise ValueError(f"Stake must be positive, got {stake}")

        account = session.query(VirtualAccount).filter(
            VirtualAccount.id == account_id
        ).with_for_update().one_or_none()

        if not account:
            raise AccountNotFoundError(f"Account {account_id} not found.")

        current_balance = float(account.balance)
        if stake > current_balance:
            raise InsufficientFundsError(requested=stake, available=current_balance)

        # 1. Create Bet record
        bet = Bet(
            account_id=account.id,
            proposal_id=proposal_id,
            event_id=event_id,
            sport_code=sport_code,
            market=market,
            outcome=outcome,
            odds=odds,
            stake=stake,
            potential_payout=round(stake * odds, 2),
            status="PENDING",
            placed_at=utc_now(),
        )
        session.add(bet)
        session.flush()

        # 2. Create immutable LedgerEntry BEFORE updating account balance
        new_balance = round(current_balance - stake, 2)
        new_exposure = round(float(account.locked_exposure) + stake, 2)

        ledger_entry = LedgerEntry(
            account_id=account.id,
            bet_id=bet.id,
            entry_type="BET_PLACED",
            amount=-stake,
            balance_after=new_balance,
            description=f"Bet placed on {sport_code} {market} ({outcome}) @ {odds:.2f}",
            created_at=utc_now(),
        )
        session.add(ledger_entry)
        session.flush()

        # 3. Update account balance and locked exposure
        account.balance = new_balance
        account.locked_exposure = new_exposure
        session.flush()

        logger.info(
            f"Placed bet {bet.id}: stake={stake:.2f}, odds={odds:.2f}, balance_after={new_balance:.2f}"
        )
        return bet

    @staticmethod
    def settle_bet(
        session: Session,
        bet_id: uuid.UUID,
        status: str,  # "WON", "LOST", "VOID"
        reason: str = "MATCH_COMPLETED",
    ) -> Tuple[Bet, BetSettlement]:
        """
        Settle pending bet, release exposure, adjust balance, and record immutable ledger entry.
        """
        status = status.upper()
        if status not in ("WON", "LOST", "VOID"):
            raise InvalidSettlementError(f"Invalid settlement status '{status}'. Must be WON, LOST, or VOID.")

        bet = session.query(Bet).filter(Bet.id == bet_id).with_for_update().one_or_none()
        if not bet:
            raise BetNotFoundError(f"Bet {bet_id} not found.")

        if bet.status != "PENDING":
            raise InvalidSettlementError(f"Bet {bet_id} is already settled with status '{bet.status}'.")

        account = session.query(VirtualAccount).filter(
            VirtualAccount.id == bet.account_id
        ).with_for_update().one()

        current_balance = float(account.balance)
        current_exposure = float(account.locked_exposure)
        stake = float(bet.stake)
        odds = float(bet.odds)

        # Release exposure for all outcomes
        new_exposure = max(0.00, round(current_exposure - stake, 2))

        if status == "WON":
            payout = round(stake * odds, 2)
            net_profit = round(payout - stake, 2)
            new_balance = round(current_balance + payout, 2)
            ledger_type = "BET_WIN"
            ledger_desc = f"Bet won: payout +{payout:.2f} RUB (profit +{net_profit:.2f}) @ {odds:.2f}"
            ledger_amount = payout
        elif status == "LOST":
            payout = 0.00
            net_profit = round(-stake, 2)
            new_balance = current_balance  # stake was already deducted upon placement
            ledger_type = "BET_LOSS"
            ledger_desc = f"Bet lost: loss -{stake:.2f} RUB"
            ledger_amount = 0.00
        else:  # VOID
            payout = stake
            net_profit = 0.00
            new_balance = round(current_balance + stake, 2)
            ledger_type = "BET_VOID"
            ledger_desc = f"Bet voided: stake refunded +{stake:.2f} RUB"
            ledger_amount = stake

        # 1. Record settlement record
        settlement = BetSettlement(
            bet_id=bet.id,
            status=status,
            payout=payout,
            net_profit=net_profit,
            settlement_reason=reason,
            settled_at=utc_now(),
        )
        session.add(settlement)

        # 2. Record immutable ledger entry BEFORE updating account balance
        ledger_entry = LedgerEntry(
            account_id=account.id,
            bet_id=bet.id,
            entry_type=ledger_type,
            amount=ledger_amount,
            balance_after=new_balance,
            description=ledger_desc,
            created_at=utc_now(),
        )
        session.add(ledger_entry)
        session.flush()

        # 3. Update account balance and locked exposure
        account.balance = new_balance
        account.locked_exposure = new_exposure
        bet.status = status
        session.flush()

        logger.info(
            f"Settled bet {bet.id} as {status}: payout={payout:.2f}, net_profit={net_profit:.2f}, "
            f"balance_after={new_balance:.2f}, exposure_after={new_exposure:.2f}"
        )
        return bet, settlement
