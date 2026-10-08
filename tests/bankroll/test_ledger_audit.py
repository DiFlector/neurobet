"""Unit and integration tests for Phase 11: Virtual bankroll & immutable ledger."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import text
from fastapi.testclient import TestClient

from db.connection import SessionLocal
from db.models.sports import Sport
from db.models.events import Event
from db.models.betting import VirtualAccount, LedgerEntry, Bet, BetSettlement, BetProposal
from db.models.ml_registry import AuditLog
from bankroll import (
    BankrollService,
    ReconciliationEngine,
    InsufficientFundsError,
    InvalidSettlementError,
)
from app.main import app


def get_or_create_test_proposal(session, stake=2500.0, odds=2.40):
    sport = session.get(Sport, "tennis")
    if not sport:
        sport = Sport(code="tennis", name="Tennis", is_primary=True)
        session.add(sport)
        session.flush()

    evt_source_id = f"evt_test_{uuid.uuid4().hex[:8]}"
    event = Event(
        source="fonbet",
        source_event_id=evt_source_id,
        sport_code="tennis",
        participant_a_name="Player A",
        participant_b_name="Player B",
        scheduled_start_at=datetime.now(timezone.utc),
        status="live",
        is_live=True,
    )
    session.add(event)
    session.flush()

    proposal = BetProposal(
        event_id=event.id,
        sport_code="tennis",
        market="match_winner",
        outcome="player_a",
        selection_id=uuid.uuid4(),
        bookmaker_odds=odds,
        fair_odds=round(odds * 0.9, 2),
        model_probability=0.55,
        edge=0.10,
        suggested_stake=stake,
    )
    session.add(proposal)
    session.flush()
    return event, proposal


def test_virtual_account_initialization_and_deposit():
    """Verify virtual account creation and initial deposit ledger entry."""
    with SessionLocal() as session:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=50000.0)
        session.commit()

        assert account.name == acc_name
        assert float(account.initial_balance) == 50000.0
        assert float(account.balance) == 50000.0
        assert float(account.locked_exposure) == 0.0

        # Check first ledger entry
        first_entry = session.query(LedgerEntry).filter(
            LedgerEntry.account_id == account.id
        ).order_by(LedgerEntry.created_at.asc()).first()

        assert first_entry is not None
        assert first_entry.entry_type == "INITIAL_DEPOSIT"
        assert float(first_entry.amount) == 50000.0
        assert float(first_entry.balance_after) == 50000.0

    print("test_virtual_account_initialization_and_deposit: OK")


def test_bet_placement_deducts_balance_and_creates_exposure():
    """Verify bet placement reduces balance, increases exposure, and records BET_PLACED."""
    with SessionLocal() as session:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=100000.0)
        event, proposal = get_or_create_test_proposal(session, stake=2500.0, odds=2.40)
        session.commit()

        # Place bet with 2,500 RUB stake @ 2.40 odds
        bet = BankrollService.place_bet(
            session=session,
            account_id=account.id,
            proposal_id=proposal.id,
            event_id=event.id,
            sport_code="tennis",
            market="match_winner",
            outcome="player_a",
            odds=2.40,
            stake=2500.0,
        )
        session.commit()

        # Reload account
        session.refresh(account)
        assert float(account.balance) == 97500.0
        assert float(account.locked_exposure) == 2500.0
        assert bet.status == "PENDING"
        assert float(bet.potential_payout) == 6000.0

        # Check BET_PLACED ledger entry
        latest_entry = session.query(LedgerEntry).filter(
            LedgerEntry.account_id == account.id,
            LedgerEntry.bet_id == bet.id,
        ).first()

        assert latest_entry is not None
        assert latest_entry.entry_type == "BET_PLACED"
        assert float(latest_entry.amount) == -2500.0
        assert float(latest_entry.balance_after) == 97500.0

        # Verify summary
        summary = BankrollService.get_account_summary(session, account.id)
        assert summary["available_balance"] == 97500.0
        assert summary["locked_exposure"] == 2500.0
        assert summary["total_equity"] == 100000.0
        assert summary["is_balanced"] is True

    print("test_bet_placement_deducts_balance_and_creates_exposure: OK")


def test_insufficient_funds_rejection():
    """Verify attempting to place a bet exceeding available balance is strictly rejected."""
    with SessionLocal() as session:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=1000.0)
        session.commit()

        raised = False
        try:
            BankrollService.place_bet(
                session=session,
                account_id=account.id,
                proposal_id=uuid.uuid4(),
                event_id=uuid.uuid4(),
                sport_code="tennis",
                market="match_winner",
                outcome="player_a",
                odds=2.00,
                stake=2500.0,  # 2,500 > 1,000 available
            )
        except InsufficientFundsError as exc:
            raised = True
            assert exc.requested == 2500.0
            assert exc.available == 1000.0

        assert raised is True, "Should have raised InsufficientFundsError"

        # Verify balance remains intact
        session.refresh(account)
        assert float(account.balance) == 1000.0
        assert float(account.locked_exposure) == 0.0

    print("test_insufficient_funds_rejection: OK")


def test_settlement_win():
    """Verify WIN settlement credits full payout, releases exposure, and records BET_WIN."""
    with SessionLocal() as session:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=10000.0)
        event, proposal = get_or_create_test_proposal(session, stake=1000.0, odds=2.50)
        session.commit()

        bet = BankrollService.place_bet(
            session=session,
            account_id=account.id,
            proposal_id=proposal.id,
            event_id=event.id,
            sport_code="tennis",
            market="match_winner",
            outcome="player_a",
            odds=2.50,
            stake=1000.0,
        )
        session.commit()
        # Balance = 9000, Exposure = 1000

        # Settle bet as WON
        settled_bet, settlement = BankrollService.settle_bet(
            session=session,
            bet_id=bet.id,
            status="WON",
        )
        session.commit()

        session.refresh(account)
        # Balance = 9000 + 2500 = 11500 RUB
        assert float(account.balance) == 11500.0
        assert float(account.locked_exposure) == 0.0
        assert settled_bet.status == "WON"
        assert float(settlement.payout) == 2500.0
        assert float(settlement.net_profit) == 1500.0

        # Check BET_WIN ledger entry
        win_entry = session.query(LedgerEntry).filter(
            LedgerEntry.bet_id == bet.id,
            LedgerEntry.entry_type == "BET_WIN",
        ).first()

        assert win_entry is not None
        assert float(win_entry.amount) == 2500.0
        assert float(win_entry.balance_after) == 11500.0

    print("test_settlement_win: OK")


def test_settlement_loss():
    """Verify LOSS settlement releases exposure, credits 0 payout, and records BET_LOSS."""
    with SessionLocal() as session:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=10000.0)
        event, proposal = get_or_create_test_proposal(session, stake=1000.0, odds=2.00)
        session.commit()

        bet = BankrollService.place_bet(
            session=session,
            account_id=account.id,
            proposal_id=proposal.id,
            event_id=event.id,
            sport_code="tennis",
            market="match_winner",
            outcome="player_a",
            odds=2.00,
            stake=1000.0,
        )
        session.commit()
        # Balance = 9000, Exposure = 1000

        # Settle bet as LOST
        settled_bet, settlement = BankrollService.settle_bet(
            session=session,
            bet_id=bet.id,
            status="LOST",
        )
        session.commit()

        session.refresh(account)
        # Balance remains 9000 (stake was deducted on placement), Exposure = 0
        assert float(account.balance) == 9000.0
        assert float(account.locked_exposure) == 0.0
        assert settled_bet.status == "LOST"
        assert float(settlement.payout) == 0.0
        assert float(settlement.net_profit) == -1000.0

        # Check BET_LOSS ledger entry
        loss_entry = session.query(LedgerEntry).filter(
            LedgerEntry.bet_id == bet.id,
            LedgerEntry.entry_type == "BET_LOSS",
        ).first()

        assert loss_entry is not None
        assert float(loss_entry.amount) == 0.0
        assert float(loss_entry.balance_after) == 9000.0

    print("test_settlement_loss: OK")


def test_settlement_void():
    """Verify VOID settlement refunds stake, releases exposure, and records BET_VOID."""
    with SessionLocal() as session:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=10000.0)
        event, proposal = get_or_create_test_proposal(session, stake=1500.0, odds=3.00)
        session.commit()

        bet = BankrollService.place_bet(
            session=session,
            account_id=account.id,
            proposal_id=proposal.id,
            event_id=event.id,
            sport_code="tennis",
            market="match_winner",
            outcome="player_a",
            odds=3.00,
            stake=1500.0,
        )
        session.commit()
        # Balance = 8500, Exposure = 1500

        # Settle bet as VOID
        settled_bet, settlement = BankrollService.settle_bet(
            session=session,
            bet_id=bet.id,
            status="VOID",
            reason="MATCH_ABANDONED_RETIRED",
        )
        session.commit()

        session.refresh(account)
        # Balance refunded = 8500 + 1500 = 10000 RUB, Exposure = 0
        assert float(account.balance) == 10000.0
        assert float(account.locked_exposure) == 0.0
        assert settled_bet.status == "VOID"
        assert float(settlement.payout) == 1500.0
        assert float(settlement.net_profit) == 0.0

        # Check BET_VOID ledger entry
        void_entry = session.query(LedgerEntry).filter(
            LedgerEntry.bet_id == bet.id,
            LedgerEntry.entry_type == "BET_VOID",
        ).first()

        assert void_entry is not None
        assert float(void_entry.amount) == 1500.0
        assert float(void_entry.balance_after) == 10000.0

    print("test_settlement_void: OK")


def test_immutable_ledger_trigger_blocks_tampering():
    """Verify database triggers block direct balance updates and ledger deletions."""
    with SessionLocal() as session:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=50000.0)
        session.commit()

        # 1. Attempt direct balance update in SQL without corresponding ledger entries
        try:
            session.execute(
                text("UPDATE virtual_accounts SET balance = 999999.00 WHERE id = :id"),
                {"id": account.id},
            )
            session.commit()
            assert False, "Direct balance UPDATE should have been blocked by trg_verify_virtual_account_balance"
        except Exception as e:
            session.rollback()
            assert "DirectBalanceUpdateViolation" in str(e)

        # 2. Attempt to delete a ledger entry
        entry = session.query(LedgerEntry).filter(LedgerEntry.account_id == account.id).first()
        try:
            session.execute(
                text("DELETE FROM ledger_entries WHERE id = :id"),
                {"id": entry.id},
            )
            session.commit()
            assert False, "DELETE on ledger_entries should have been blocked by trg_prevent_ledger_entry_mutation"
        except Exception as e:
            session.rollback()
            assert "ImmutableLedgerViolation" in str(e)

    print("test_immutable_ledger_trigger_blocks_tampering: OK")


def test_reconciliation_detects_and_verifies_audit_trail():
    """Verify ReconciliationEngine validates balance consistency and logs audit trail."""
    with SessionLocal() as session:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=20000.0)
        e1, p1 = get_or_create_test_proposal(session, stake=1000.0, odds=2.00)
        e2, p2 = get_or_create_test_proposal(session, stake=500.0, odds=1.80)
        session.commit()

        # Run several operations: bet placement, win, bet placement (pending)
        b1 = BankrollService.place_bet(
            session=session,
            account_id=account.id,
            proposal_id=p1.id,
            event_id=e1.id,
            sport_code="tennis",
            market="match_winner",
            outcome="player_a",
            odds=2.00,
            stake=1000.0,
        )
        session.commit()

        BankrollService.settle_bet(session=session, bet_id=b1.id, status="WON")
        session.commit()

        b2 = BankrollService.place_bet(
            session=session,
            account_id=account.id,
            proposal_id=p2.id,
            event_id=e2.id,
            sport_code="tennis",
            market="match_winner",
            outcome="player_b",
            odds=1.80,
            stake=500.0,
        )
        session.commit()

        # Reconcile account
        report = ReconciliationEngine.reconcile_account(session, account.id)
        session.commit()

        assert report.is_balance_reconciled is True
        assert report.is_exposure_reconciled is True
        assert report.balance_difference == 0.0
        assert report.exposure_difference == 0.0
        assert report.actual_exposure == 500.0
        assert report.pending_bets_count == 1
        assert report.settled_bets_count == 1
        assert len(report.discrepancies) == 0

        # Check audit log entry
        audit_entry = session.query(AuditLog).filter(
            AuditLog.resource_id == str(account.id),
            AuditLog.action == "BANKROLL_RECONCILIATION",
        ).order_by(AuditLog.created_at.desc()).first()

        assert audit_entry is not None
        assert audit_entry.details["status"] == "SUCCESS"

    print("test_reconciliation_detects_and_verifies_audit_trail: OK")


def test_backend_api_bankroll_and_reconciliation():
    """Verify FastAPI endpoints /api/bankroll, /api/bankroll/ledger, /api/bankroll/reconcile."""
    client = TestClient(app)

    # 1. GET /api/bankroll
    res = client.get("/api/bankroll")
    assert res.status_code == 200
    data = res.json()
    assert "available_balance" in data
    assert "locked_exposure" in data
    assert "total_equity" in data
    assert data["is_balanced"] is True

    # 2. GET /api/bankroll/ledger
    res_ledger = client.get("/api/bankroll/ledger")
    assert res_ledger.status_code == 200
    ledger_entries = res_ledger.json()
    assert isinstance(ledger_entries, list)
    assert len(ledger_entries) > 0
    assert "entry_type" in ledger_entries[0]

    # 3. POST /api/bankroll/reconcile
    res_rec = client.post("/api/bankroll/reconcile")
    assert res_rec.status_code == 200
    rec_report = res_rec.json()
    assert rec_report["is_balance_reconciled"] is True
    assert rec_report["is_exposure_reconciled"] is True

    print("test_backend_api_bankroll_and_reconciliation: OK")


if __name__ == "__main__":
    test_virtual_account_initialization_and_deposit()
    test_bet_placement_deducts_balance_and_creates_exposure()
    test_insufficient_funds_rejection()
    test_settlement_win()
    test_settlement_loss()
    test_settlement_void()
    test_immutable_ledger_trigger_blocks_tampering()
    test_reconciliation_detects_and_verifies_audit_trail()
    test_backend_api_bankroll_and_reconciliation()
    print("ALL BANKROLL & IMMUTABLE LEDGER TESTS PASSED!")
