"""Unit and integration test suite for Phase 13: Settlement and Fonbet Results Collection."""

from datetime import datetime, timezone, timedelta
import random
import uuid
from fastapi.testclient import TestClient

from db.connection import SessionLocal
from db.models.sports import Sport
from db.models.events import Event
from db.models.betting import VirtualAccount, LedgerEntry, Bet, BetSettlement, BetProposal
from contracts import CanonicalMatchResult
from sports_core import FonbetResultsParser
from bankroll import BankrollService, EventResultMatcher, BetSettlementEngine
from app.main import app


def utc_now():
    return datetime.now(timezone.utc)


def setup_test_event_and_bet(session, outcome="player_a", stake=1000.0, odds=2.20):
    sport = session.get(Sport, "tennis")
    if not sport:
        sport = Sport(code="tennis", name="Tennis", is_primary=True)
        session.add(sport)
        session.flush()

    account = BankrollService.get_or_create_account(
        session,
        name=f"settle_acc_{uuid.uuid4().hex[:8]}",
        initial_balance=50000.0,
    )
    session.flush()

    source_id = str(random.randint(10000000, 99999999))
    event = Event(
        source="fonbet",
        source_event_id=source_id,
        sport_code="tennis",
        participant_a_name="Daniil Medvedev",
        participant_b_name="Carlos Alcaraz",
        scheduled_start_at=utc_now() - timedelta(hours=2),
        first_seen_at=utc_now() - timedelta(hours=2),
        last_seen_at=utc_now() - timedelta(minutes=10),
        status="live",
        is_live=True,
    )
    session.add(event)
    session.flush()

    proposal = BetProposal(
        id=uuid.uuid4(),
        event_id=event.id,
        sport_code="tennis",
        market="match_winner",
        outcome=outcome,
        selection_id=uuid.uuid4(),
        bookmaker_odds=odds,
        fair_odds=1.85,
        model_probability=0.55,
        edge=0.08,
        suggested_stake=stake,
    )
    session.add(proposal)
    session.flush()

    bet = BankrollService.place_bet(
        session=session,
        account_id=account.id,
        proposal_id=proposal.id,
        event_id=event.id,
        sport_code="tennis",
        market="match_winner",
        outcome=outcome,
        odds=odds,
        stake=stake,
    )
    session.commit()
    return account, event, bet


def test_fonbet_results_feed_parsing():
    """Verify parsing of official results JSON payload from fon.bet/results."""
    sample_feed = {
        "sports": [
            {"id": "3", "name": "Теннис", "fonbetId": 4}
        ],
        "sections": [
            {
                "id": 101,
                "name": "Теннис. ATP. Шанхай. Хард",
                "fonbetSportId": 4,
                "events": [201, 202, 203]
            }
        ],
        "events": [
            {
                "id": "201",
                "name": "Медведев Д. – Алькарас К.",
                "score": "2:1 (6-4 3-6 6-4)",
                "status": 3,
                "comment1": "",
                "comment2": "",
                "comment3": "",
            },
            {
                "id": "202",
                "name": "эйсы",
                "score": "10:5 (4-2 3-2 3-1)",
                "status": 3,
            },
            {
                "id": "203",
                "name": "Синнер Я. – Джокович Н.",
                "score": "0:2 (4-6 3-6)",
                "status": 3,
                "comment1": "",
                "comment2": "",
                "comment3": "",
            }
        ]
    }

    results = FonbetResultsParser.parse_feed(sample_feed)
    assert len(results) == 2, f"Expected 2 matches, got {len(results)}"

    r1 = results[0]
    assert r1.source_event_id == "201"
    assert r1.participant_a == "Медведев Д."
    assert r1.participant_b == "Алькарас К."
    assert r1.final_score == "2:1 (6-4 3-6 6-4)"
    assert r1.winner == "player_a"
    assert r1.status == "FINISHED"

    r2 = results[1]
    assert r2.source_event_id == "203"
    assert r2.participant_a == "Синнер Я."
    assert r2.participant_b == "Джокович Н."
    assert r2.winner == "player_b"
    assert r2.status == "FINISHED"
    print("test_fonbet_results_feed_parsing: OK")


def test_retirement_and_walkover_detection():
    """Verify official Fonbet retirement and walkover parsing."""
    sample_feed = {
        "sections": [
            {"id": 1, "name": "Теннис. Турнир", "fonbetSportId": 4, "events": [1, 2]}
        ],
        "events": [
            {
                "id": "1",
                "name": "Зверев А. – Рублев А.",
                "score": "1:0 (6-3 2-1)",
                "status": 3,
                "comment1": "Отказ Рублев А.",
                "comment2": "",
                "comment3": "",
            },
            {
                "id": "2",
                "name": "Циципас С. – Хачанов К.",
                "score": "-:-",
                "status": 3,
                "comment1": "Матч не состоялся",
                "comment2": "",
                "comment3": "",
            }
        ]
    }

    results = FonbetResultsParser.parse_feed(sample_feed)
    assert len(results) == 2

    # Retired match
    r1 = results[0]
    assert r1.is_retired is True
    assert r1.winner == "player_a"

    # Walkover match
    r2 = results[1]
    assert r2.is_walkover is True
    assert r2.status == "CANCELLED"
    print("test_retirement_and_walkover_detection: OK")


def test_event_result_matcher_marks_finished():
    """Verify EventResultMatcher updates event state to finished with official score."""
    session = SessionLocal()
    try:
        account, event, bet = setup_test_event_and_bet(session)

        canonical = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="2:1 (6-4 4-6 6-3)",
            winner="player_a",
            status="FINISHED",
        )

        matched = EventResultMatcher.match_and_complete_event(session, canonical)
        assert matched is not None
        assert matched.id == event.id
        assert matched.status == "finished"
        assert matched.finished_at is not None
        assert matched.current_score["final_score"] == "2:1 (6-4 4-6 6-3)"
        assert matched.current_score["winner"] == "player_a"
        session.commit()
        print("test_event_result_matcher_marks_finished: OK")
    finally:
        session.close()


def test_settlement_win():
    """Verify WIN settlement credits payout, records BET_WIN in ledger, and releases exposure."""
    session = SessionLocal()
    try:
        account, event, bet = setup_test_event_and_bet(session, outcome="player_a", stake=1000.0, odds=2.50)
        initial_balance = float(account.balance)
        initial_exposure = float(account.locked_exposure)

        canonical = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="2:0 (6-4 6-3)",
            winner="player_a",
            status="FINISHED",
        )

        EventResultMatcher.match_and_complete_event(session, canonical)
        settlements = BetSettlementEngine.settle_event_bets(session, event, canonical)
        session.commit()

        assert len(settlements) == 1
        s = settlements[0]
        assert s.status == "WON"
        assert s.payout == 2500.0  # 1000 * 2.50
        assert s.net_profit == 1500.0

        # Check account state
        acc_updated = session.get(VirtualAccount, account.id)
        assert float(acc_updated.balance) == initial_balance + 2500.0
        assert float(acc_updated.locked_exposure) == initial_exposure - 1000.0

        # Check immutable ledger
        last_entry = (
            session.query(LedgerEntry)
            .filter(LedgerEntry.bet_id == bet.id)
            .order_by(LedgerEntry.created_at.desc())
            .first()
        )
        assert last_entry.entry_type == "BET_WIN"
        assert float(last_entry.amount) == 2500.0
        print("test_settlement_win: OK")
    finally:
        session.close()


def test_settlement_loss():
    """Verify LOST settlement records BET_LOSS in ledger and releases exposure without payout."""
    session = SessionLocal()
    try:
        account, event, bet = setup_test_event_and_bet(session, outcome="player_b", stake=1500.0, odds=2.20)
        initial_balance = float(account.balance)
        initial_exposure = float(account.locked_exposure)

        # Result winner is player_a -> player_b lost
        canonical = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="2:1 (6-4 3-6 6-4)",
            winner="player_a",
            status="FINISHED",
        )

        EventResultMatcher.match_and_complete_event(session, canonical)
        settlements = BetSettlementEngine.settle_event_bets(session, event, canonical)
        session.commit()

        assert len(settlements) == 1
        s = settlements[0]
        assert s.status == "LOST"
        assert s.payout == 0.0
        assert s.net_profit == -1500.0

        # Account state: balance unchanged (stake was deducted upon placement), exposure released
        acc_updated = session.get(VirtualAccount, account.id)
        assert float(acc_updated.balance) == initial_balance
        assert float(acc_updated.locked_exposure) == initial_exposure - 1500.0

        # Ledger check
        last_entry = (
            session.query(LedgerEntry)
            .filter(LedgerEntry.bet_id == bet.id)
            .order_by(LedgerEntry.created_at.desc())
            .first()
        )
        assert last_entry.entry_type == "BET_LOSS"
        assert float(last_entry.amount) == 0.0
        print("test_settlement_loss: OK")
    finally:
        session.close()


def test_settlement_void():
    """Verify VOID settlement refunds stake and releases exposure."""
    session = SessionLocal()
    try:
        account, event, bet = setup_test_event_and_bet(session, outcome="player_a", stake=2000.0, odds=2.00)
        initial_balance = float(account.balance)
        initial_exposure = float(account.locked_exposure)

        canonical = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="-:-",
            winner=None,
            status="CANCELLED",
            comments="Матч отменен организаторами",
        )

        EventResultMatcher.match_and_complete_event(session, canonical)
        settlements = BetSettlementEngine.settle_event_bets(session, event, canonical)
        session.commit()

        assert len(settlements) == 1
        s = settlements[0]
        assert s.status == "VOID"
        assert s.payout == 2000.0  # refund
        assert s.net_profit == 0.0

        acc_updated = session.get(VirtualAccount, account.id)
        assert float(acc_updated.balance) == initial_balance + 2000.0
        assert float(acc_updated.locked_exposure) == initial_exposure - 2000.0

        last_entry = (
            session.query(LedgerEntry)
            .filter(LedgerEntry.bet_id == bet.id)
            .order_by(LedgerEntry.created_at.desc())
            .first()
        )
        assert last_entry.entry_type == "BET_VOID"
        assert float(last_entry.amount) == 2000.0
        print("test_settlement_void: OK")
    finally:
        session.close()


def test_strict_idempotency_double_settlement_prevented():
    """Verify that settling the same bet twice returns existing settlement without duplicate ledger entries."""
    session = SessionLocal()
    try:
        account, event, bet = setup_test_event_and_bet(session, outcome="player_a", stake=1000.0, odds=2.00)

        canonical = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="2:0 (6-4 6-2)",
            winner="player_a",
            status="FINISHED",
        )

        # First settlement
        EventResultMatcher.match_and_complete_event(session, canonical)
        settlements_1 = BetSettlementEngine.settle_event_bets(session, event, canonical)
        session.commit()

        balance_after_1 = float(session.get(VirtualAccount, account.id).balance)
        ledger_count_1 = session.query(LedgerEntry).filter(LedgerEntry.bet_id == bet.id).count()

        # Second settlement attempt (identical payload)
        settlements_2 = BetSettlementEngine.settle_event_bets(session, event, canonical)
        session.commit()

        balance_after_2 = float(session.get(VirtualAccount, account.id).balance)
        ledger_count_2 = session.query(LedgerEntry).filter(LedgerEntry.bet_id == bet.id).count()

        assert balance_after_1 == balance_after_2, "Balance changed upon duplicate settlement!"
        assert ledger_count_1 == ledger_count_2, "Duplicate ledger entry created!"
        assert len(settlements_2) == 1
        assert settlements_2[0].id == settlements_1[0].id
        print("test_strict_idempotency_double_settlement_prevented: OK")
    finally:
        session.close()


def test_settlement_review_required_for_ambiguous_results():
    """Verify that ambiguous match scores are flagged for review and never settled blindly."""
    session = SessionLocal()
    try:
        account, event, bet = setup_test_event_and_bet(session, outcome="player_a", stake=1000.0, odds=2.00)

        # Incomplete / disputed score
        canonical = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="-:-",
            winner=None,
            status="SETTLEMENT_REVIEW_REQUIRED",
            comments="Результат матча уточняется",
        )

        EventResultMatcher.match_and_complete_event(session, canonical)
        settlements = BetSettlementEngine.settle_event_bets(session, event, canonical)
        session.commit()

        # Bet must remain PENDING
        assert len(settlements) == 0, "Ambiguous match must not produce settlements!"
        updated_bet = session.get(Bet, bet.id)
        assert updated_bet.status == "PENDING"

        # Event must be present in review queue
        review_queue = BetSettlementEngine.get_review_queue(session)
        assert any(q["event_id"] == str(event.id) for q in review_queue)
        print("test_settlement_review_required_for_ambiguous_results: OK")
    finally:
        session.close()


def test_backend_settlement_api_endpoints():
    """Verify backend API settlement endpoints."""
    session = SessionLocal()
    try:
        account, event, bet = setup_test_event_and_bet(session, outcome="player_a", stake=1200.0, odds=2.40)

        client = TestClient(app)

        # 1. Trigger sync with official results payload
        payload = {
            "sections": [
                {"id": 1, "name": "Теннис. Турнир", "fonbetSportId": 4, "events": [int(event.source_event_id)]}
            ],
            "events": [
                {
                    "id": event.source_event_id,
                    "name": "Daniil Medvedev – Carlos Alcaraz",
                    "score": "2:0 (6-4 6-4)",
                    "status": 3,
                }
            ]
        }
        sync_resp = client.post("/api/settlement/sync", json=payload)
        assert sync_resp.status_code == 200
        sync_data = sync_resp.json()
        assert sync_data["status"] == "success"
        assert sync_data["events_matched"] == 1
        assert sync_data["bets_settled"] == 1

        # 2. Check settlement history
        hist_resp = client.get("/api/settlement/history")
        assert hist_resp.status_code == 200
        hist_data = hist_resp.json()
        assert any(h["bet_id"] == str(bet.id) for h in hist_data)

        # 3. Check review queue endpoint
        rev_resp = client.get("/api/settlement/review-queue")
        assert rev_resp.status_code == 200
        assert isinstance(rev_resp.json(), list)
        print("test_backend_settlement_api_endpoints: OK")
    finally:
        session.close()


if __name__ == "__main__":
    print("\n--- Running Phase 13 Settlement & Fonbet Results Tests ---")
    test_fonbet_results_feed_parsing()
    test_retirement_and_walkover_detection()
    test_event_result_matcher_marks_finished()
    test_settlement_win()
    test_settlement_loss()
    test_settlement_void()
    test_strict_idempotency_double_settlement_prevented()
    test_settlement_review_required_for_ambiguous_results()
    test_backend_settlement_api_endpoints()
    print("\nALL SETTLEMENT & RESULTS TESTS PASSED SUCCESSFULLY!")
