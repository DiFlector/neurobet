"""
Test Balance Reconstruction from Immutable Ledger for Phase 31: Disaster Recovery.
Verifies that:
1. Virtual account balance is strictly mathematical identity:
   balance = initial_balance + sum(ledger_entries.amount)
2. In case of database balance corruption, the account balance and locked exposure
   can be completely and deterministically reconstructed from the immutable journal.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import text
from db.connection import SessionLocal
from db.models.sports import Sport
from db.models.events import Event
from db.models.betting import VirtualAccount, LedgerEntry, Bet, BetProposal, BetSettlement
from bankroll import BankrollService, ReconciliationEngine

def get_or_create_sport(session, code="tennis"):
    sport = session.get(Sport, code)
    if not sport:
        sport = Sport(code=code, name=code.capitalize(), is_primary=True)
        session.add(sport)
        session.flush()
    return sport

def create_event_and_proposal(session, stake=5000.0, odds=2.00, sport_code="tennis"):
    get_or_create_sport(session, sport_code)
    event = Event(
        source="fonbet",
        source_event_id=f"dr_evt_{uuid.uuid4().hex[:8]}",
        sport_code=sport_code,
        participant_a_name="Team Alpha",
        participant_b_name="Team Beta",
        scheduled_start_at=datetime.now(timezone.utc),
        status="live",
        is_live=True,
    )
    session.add(event)
    session.flush()

    proposal = BetProposal(
        event_id=event.id,
        sport_code=sport_code,
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

def test_balance_reconstruction():
    print("============================================================")
    print("🧪 TEST: BALANCE RECONSTRUCTION FROM IMMUTABLE LEDGER")
    print("============================================================")

    with SessionLocal() as session:
        acc_name = f"dr_acc_{uuid.uuid4().hex[:8]}"
        initial_balance = 100000.00
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=initial_balance)
        session.commit()
        print(f"[*] Created account '{acc_name}' with initial deposit {initial_balance:,.2f} RUB")

        # --- Scenario 1: Bet 1 (5,000 RUB @ 2.00 -> WON) ---
        e1, p1 = create_event_and_proposal(session, stake=5000.0, odds=2.00)
        b1 = BankrollService.place_bet(session, account.id, p1.id, e1.id, "tennis", "match_winner", "player_a", 2.00, 5000.0)
        session.commit()
        BankrollService.settle_bet(session, b1.id, "WON", reason="MATCH_WON")
        session.commit()
        # Balance = 100,000 - 5,000 + 10,000 = 105,000

        # --- Scenario 2: Bet 2 (8,000 RUB @ 1.80 -> LOST) ---
        e2, p2 = create_event_and_proposal(session, stake=8000.0, odds=1.80)
        b2 = BankrollService.place_bet(session, account.id, p2.id, e2.id, "tennis", "match_winner", "player_a", 1.80, 8000.0)
        session.commit()
        BankrollService.settle_bet(session, b2.id, "LOST", reason="MATCH_LOST")
        session.commit()
        # Balance = 105,000 - 8,000 + 0 = 97,000

        # --- Scenario 3: Bet 3 (4,000 RUB @ 2.50 -> VOID) ---
        e3, p3 = create_event_and_proposal(session, stake=4000.0, odds=2.50)
        b3 = BankrollService.place_bet(session, account.id, p3.id, e3.id, "tennis", "match_winner", "player_a", 2.50, 4000.0)
        session.commit()
        BankrollService.settle_bet(session, b3.id, "VOID", reason="MATCH_CANCELLED")
        session.commit()
        # Balance = 97,000 - 4,000 + 4,000 = 97,000

        # --- Scenario 4: Bet 4 (6,000 RUB @ 2.10 -> PENDING) ---
        e4, p4 = create_event_and_proposal(session, stake=6000.0, odds=2.10)
        b4 = BankrollService.place_bet(session, account.id, p4.id, e4.id, "tennis", "match_winner", "player_a", 2.10, 6000.0)
        session.commit()
        # Balance = 97,000 - 6,000 = 91,000, Exposure = 6,000

        # 1. Normal reconciliation check
        rep1 = ReconciliationEngine.reconcile_account(session, account.id)
        assert rep1.is_balance_reconciled is True
        assert rep1.is_exposure_reconciled is True
        assert rep1.actual_balance == 91000.00
        assert rep1.recalculated_balance == 91000.00
        assert rep1.actual_exposure == 6000.00
        assert rep1.recalculated_exposure == 6000.00
        print(f"[+] Normal account state verified: Balance={rep1.actual_balance} RUB, Exposure={rep1.actual_exposure} RUB")

        # 2. Simulate catastrophic corruption of account state (disabling trigger to simulate storage bit-rot/crash)
        print("[*] Simulating catastrophic database corruption on account balance...")
        session.execute(text("ALTER TABLE virtual_accounts DISABLE TRIGGER ALL;"))
        session.execute(text(
            f"UPDATE virtual_accounts SET balance = 0.00, locked_exposure = 999999.00 WHERE id = '{account.id}'"
        ))
        session.commit()

        # Reload corrupted state
        corrupted_acc = session.get(VirtualAccount, account.id)
        assert float(corrupted_acc.balance) == 0.00
        assert float(corrupted_acc.locked_exposure) == 999999.00
        print(f"[!] Account corrupted: balance={corrupted_acc.balance}, exposure={corrupted_acc.locked_exposure}")

        # Verification detects discrepancy
        rep2 = ReconciliationEngine.reconcile_account(session, account.id)
        assert rep2.is_balance_reconciled is False
        assert rep2.is_exposure_reconciled is False
        assert rep2.balance_difference == -91000.00
        print(f"[+] Reconciliation correctly flagged discrepancy: diff={rep2.balance_difference} RUB")

        # 3. Disaster Recovery Reconstruction Routine
        print("[*] Executing disaster recovery balance reconstruction routine...")
        # Step A: Replay immutable ledger
        ledger_entries = session.query(LedgerEntry).filter(
            LedgerEntry.account_id == account.id
        ).order_by(LedgerEntry.created_at.asc()).all()

        reconstructed_balance = float(account.initial_balance)
        for entry in ledger_entries:
            if entry.entry_type == "INITIAL_DEPOSIT":
                reconstructed_balance = float(entry.amount)
            else:
                reconstructed_balance = round(reconstructed_balance + float(entry.amount), 2)

        # Step B: Recompute active exposure from pending bets
        pending_bets = session.query(Bet).filter(
            Bet.account_id == account.id,
            Bet.status == "PENDING"
        ).all()
        reconstructed_exposure = round(sum(float(b.stake) for b in pending_bets), 2)

        # Step C: Repair and restore the virtual account
        repaired_acc = session.get(VirtualAccount, account.id)
        repaired_acc.balance = reconstructed_balance
        repaired_acc.locked_exposure = reconstructed_exposure
        session.commit()

        # Step D: Re-enable triggers
        session.execute(text("ALTER TABLE virtual_accounts ENABLE TRIGGER ALL;"))
        session.commit()
        print(f"[+] Account repaired: restored balance={reconstructed_balance} RUB, exposure={reconstructed_exposure} RUB")

        # 4. Final verification after repair
        rep3 = ReconciliationEngine.reconcile_account(session, account.id)
        assert rep3.is_balance_reconciled is True
        assert rep3.is_exposure_reconciled is True
        assert rep3.actual_balance == 91000.00
        assert rep3.recalculated_balance == 91000.00
        assert rep3.actual_exposure == 6000.00
        assert rep3.recalculated_exposure == 6000.00
        assert len(rep3.discrepancies) == 0

        print(f"[+] Post-repair reconciliation: Status=PASSED, 0 discrepancies!")

    print("============================================================")
    print("✅ TEST BALANCE RECONSTRUCTION: PASSED")
    print("============================================================")

if __name__ == "__main__":
    test_balance_reconstruction()
