import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from db.models.betting import VirtualAccount, LedgerEntry, Bet
from db.models.ml_registry import AuditLog
from .exceptions import AccountNotFoundError

logger = logging.getLogger("neurobet.bankroll.reconciliation")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LedgerDiscrepancy(BaseModel):
    entry_id: str
    expected_balance_after: float
    actual_balance_after: float
    discrepancy: float
    entry_type: str
    amount: float


class ReconciliationReport(BaseModel):
    account_id: str
    account_name: str
    initial_balance: float
    actual_balance: float
    recalculated_balance: float
    balance_difference: float
    is_balance_reconciled: bool

    actual_exposure: float
    recalculated_exposure: float
    exposure_difference: float
    is_exposure_reconciled: bool

    total_ledger_entries: int
    pending_bets_count: int
    settled_bets_count: int

    discrepancies: List[LedgerDiscrepancy] = Field(default_factory=list)
    reconciled_at: datetime = Field(default_factory=utc_now)


class ReconciliationEngine:
    """
    Performs full audits on virtual accounts by reconstructing balance and exposure
    from first principles directly from the immutable ledger journal.
    """

    @classmethod
    def reconcile_account(cls, session: Session, account_id: uuid.UUID) -> ReconciliationReport:
        """
        Verify that account balance exactly matches the sum of its ledger entries,
        and locked exposure matches the sum of pending bets.
        """
        account = session.get(VirtualAccount, account_id)
        if not account:
            raise AccountNotFoundError(f"Account {account_id} not found.")

        # 1. Retrieve all ledger entries in strict chronological order
        entries = session.query(LedgerEntry).filter(
            LedgerEntry.account_id == account.id
        ).order_by(LedgerEntry.created_at.asc()).all()

        running_balance = float(account.initial_balance)
        discrepancies: List[LedgerDiscrepancy] = []

        for entry in entries:
            amt = float(entry.amount)
            if entry.entry_type == "INITIAL_DEPOSIT":
                running_balance = amt
            else:
                running_balance = round(running_balance + amt, 2)

            recorded_balance_after = float(entry.balance_after)
            diff = round(recorded_balance_after - running_balance, 2)
            if abs(diff) > 0.01:
                discrepancies.append(
                    LedgerDiscrepancy(
                        entry_id=str(entry.id),
                        expected_balance_after=running_balance,
                        actual_balance_after=recorded_balance_after,
                        discrepancy=diff,
                        entry_type=entry.entry_type,
                        amount=amt,
                    )
                )

        # 2. Check pending bets for exposure verification
        pending_bets = session.query(Bet).filter(
            Bet.account_id == account.id,
            Bet.status == "PENDING",
        ).all()

        settled_bets_cnt = session.query(func.count(Bet.id)).filter(
            Bet.account_id == account.id,
            Bet.status != "PENDING",
        ).scalar() or 0

        recalculated_exposure = round(sum(float(b.stake) for b in pending_bets), 2)
        actual_exposure = round(float(account.locked_exposure), 2)
        exposure_diff = round(actual_exposure - recalculated_exposure, 2)

        actual_balance = round(float(account.balance), 2)
        balance_diff = round(actual_balance - running_balance, 2)

        is_balance_ok = abs(balance_diff) < 0.01 and len(discrepancies) == 0
        is_exposure_ok = abs(exposure_diff) < 0.01

        report = ReconciliationReport(
            account_id=str(account.id),
            account_name=account.name,
            initial_balance=float(account.initial_balance),
            actual_balance=actual_balance,
            recalculated_balance=running_balance,
            balance_difference=balance_diff,
            is_balance_reconciled=is_balance_ok,
            actual_exposure=actual_exposure,
            recalculated_exposure=recalculated_exposure,
            exposure_difference=exposure_diff,
            is_exposure_reconciled=is_exposure_ok,
            total_ledger_entries=len(entries),
            pending_bets_count=len(pending_bets),
            settled_bets_count=settled_bets_cnt,
            discrepancies=discrepancies,
        )

        # 3. Log audit event
        audit_status = "SUCCESS" if (is_balance_ok and is_exposure_ok) else "DISCREPANCY_DETECTED"
        audit = AuditLog(
            action="BANKROLL_RECONCILIATION",
            actor="system",
            resource_type="virtual_account",
            resource_id=str(account.id),
            details={
                "status": audit_status,
                "is_balance_reconciled": is_balance_ok,
                "is_exposure_reconciled": is_exposure_ok,
                "balance_diff": balance_diff,
                "exposure_diff": exposure_diff,
                "discrepancies_count": len(discrepancies),
            },
            created_at=utc_now(),
        )
        session.add(audit)
        session.flush()

        logger.info(
            f"Reconciliation for account '{account.name}': Balance Reconciled={is_balance_ok}, "
            f"Exposure Reconciled={is_exposure_ok}, Diff={balance_diff:.2f} RUB"
        )
        return report
