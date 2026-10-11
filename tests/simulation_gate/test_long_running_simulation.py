"""
Phase 32: Long-Running Simulation Stress Test.
Validates requirement: "Long-running simulation produces no balance corruption."
Simulates 500+ bets across all 5 sports (Tennis, Basketball, Volleyball, Hockey, Football)
with diverse settlement outcomes (WON, LOST, VOID), concurrent pending exposures,
and executes periodic and final financial ledger audits.
"""

import random
import uuid
from datetime import datetime, timezone
from db.connection import SessionLocal
from db.models.sports import Sport
from db.models.events import Event
from db.models.betting import VirtualAccount, LedgerEntry, Bet, BetProposal
from bankroll import BankrollService, ReconciliationEngine

SPORTS = ["tennis", "basketball", "volleyball", "hockey", "football"]

def seed_sports(session):
    for code in SPORTS:
        sport = session.get(Sport, code)
        if not sport:
            sport = Sport(code=code, name=code.capitalize(), is_primary=(code == "tennis"))
            session.add(sport)
    session.flush()

def test_long_running_simulation():
    print("============================================================")
    print("🧪 TEST: LONG-RUNNING SIMULATION (500 BET CYCLES)")
    print("============================================================")

    random.seed(42)  # Deterministic seed

    with SessionLocal() as session:
        seed_sports(session)
        acc_name = f"sim_gate_acc_{uuid.uuid4().hex[:8]}"
        initial_balance = 500000.00
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=initial_balance)
        session.commit()
        print(f"[*] Initialized account '{acc_name}' with {initial_balance:,.2f} RUB")

        total_cycles = 500
        placed_bets = []
        settled_count = 0
        won_count = 0
        lost_count = 0
        void_count = 0

        print(f"[*] Executing {total_cycles} simulated betting operations across 5 sports...")

        for i in range(1, total_cycles + 1):
            sport_code = random.choice(SPORTS)
            stake = round(random.uniform(500.0, 3000.0), 2)
            odds = round(random.uniform(1.30, 4.50), 2)

            # Ensure account has enough available balance for stake
            session.refresh(account)
            if float(account.balance) < stake:
                # Add adjustment or skip if exhausted
                break

            # 1. Create Event & Proposal
            evt = Event(
                source="fonbet",
                source_event_id=f"sim_evt_{i}_{uuid.uuid4().hex[:6]}",
                sport_code=sport_code,
                participant_a_name=f"Team A {i}",
                participant_b_name=f"Team B {i}",
                scheduled_start_at=datetime.now(timezone.utc),
                status="live",
                is_live=True,
            )
            session.add(evt)
            session.flush()

            proposal = BetProposal(
                event_id=evt.id,
                sport_code=sport_code,
                market="match_winner",
                outcome="team_a",
                selection_id=uuid.uuid4(),
                bookmaker_odds=odds,
                fair_odds=round(odds * 0.95, 2),
                model_probability=0.55,
                edge=0.08,
                suggested_stake=stake,
            )
            session.add(proposal)
            session.flush()

            # 2. Place Bet
            bet = BankrollService.place_bet(
                session=session,
                account_id=account.id,
                proposal_id=proposal.id,
                event_id=evt.id,
                sport_code=sport_code,
                market="match_winner",
                outcome="team_a",
                odds=odds,
                stake=stake,
            )
            session.commit()
            placed_bets.append(bet.id)

            # 3. Simulate realistic settlement for ~85% of bets (keep ~15% PENDING exposure)
            if random.random() < 0.85:
                outcome_roll = random.random()
                if outcome_roll < 0.48:
                    BankrollService.settle_bet(session, bet.id, "WON", reason="SIM_MATCH_WON")
                    won_count += 1
                elif outcome_roll < 0.92:
                    BankrollService.settle_bet(session, bet.id, "LOST", reason="SIM_MATCH_LOST")
                    lost_count += 1
                else:
                    BankrollService.settle_bet(session, bet.id, "VOID", reason="SIM_MATCH_VOID")
                    void_count += 1
                settled_count += 1
                session.commit()

            # 4. Periodic ledger audit every 100 cycles
            if i % 100 == 0:
                report = ReconciliationEngine.reconcile_account(session, account.id)
                assert report.is_balance_reconciled is True, f"Balance discrepancy at cycle {i}: diff={report.balance_difference}"
                assert report.is_exposure_reconciled is True, f"Exposure discrepancy at cycle {i}: diff={report.exposure_difference}"
                print(f"[Cycle {i}/{total_cycles}] Audit PASSED: Balance={report.actual_balance:,.2f} RUB, Exposure={report.actual_exposure:,.2f} RUB, Entries={report.total_ledger_entries}")

        session.commit()

        # Final comprehensive ledger audit
        final_report = ReconciliationEngine.reconcile_account(session, account.id)
        assert final_report.is_balance_reconciled is True
        assert final_report.is_exposure_reconciled is True
        assert abs(final_report.balance_difference) < 0.001
        assert abs(final_report.exposure_difference) < 0.001
        assert len(final_report.discrepancies) == 0

        print(f"\n================ SIMULATION STATISTICS ================")
        print(f"Total Bets Placed:    {len(placed_bets)}")
        print(f"Settled Bets:         {settled_count} (Won: {won_count}, Lost: {lost_count}, Void: {void_count})")
        print(f"Pending Exposure:     {final_report.pending_bets_count} bets ({final_report.actual_exposure:,.2f} RUB)")
        print(f"Final Balance:        {final_report.actual_balance:,.2f} RUB")
        print(f"Total Ledger Entries: {final_report.total_ledger_entries}")
        print(f"Balance Discrepancy:  {final_report.balance_difference:.4f} RUB (ZERO DRIFT)")
        print(f"=======================================================")

    print("============================================================")
    print("✅ TEST LONG-RUNNING SIMULATION: PASSED")
    print("============================================================")

if __name__ == "__main__":
    test_long_running_simulation()
