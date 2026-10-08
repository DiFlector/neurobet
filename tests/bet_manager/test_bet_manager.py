"""Comprehensive unit and integration test suite for Bet Manager service (Phase 12)."""

from datetime import datetime, timezone, timedelta
import uuid
from fastapi.testclient import TestClient

from db.connection import SessionLocal
from db.models.sports import Sport
from db.models.events import Event
from db.models.odds import Market, MarketSelection, OddsSnapshot
from db.models.betting import VirtualAccount, LedgerEntry, Bet, BetProposal as DBBetProposal, BetValidationResult as DBBetValidationResult
from bankroll.service import BankrollService

from app.config import BetManagerConfig
from app.pipeline import BetValidationPipeline
from app.executor import SimulationExecutor
from app import reject_codes
from app.main import app


def utc_now():
    return datetime.now(timezone.utc)


def setup_test_fixtures(session, status="live", is_live=True, event_age_sec=5.0, odds_age_sec=5.0, odds_val=2.20):
    sport = session.get(Sport, "tennis")
    if not sport:
        sport = Sport(code="tennis", name="Tennis", is_primary=True)
        session.add(sport)
        session.flush()

    now = utc_now()
    event = Event(
        source="fonbet",
        source_event_id=f"evt_bm_{uuid.uuid4().hex[:8]}",
        sport_code="tennis",
        participant_a_name="Daniil Medvedev",
        participant_b_name="Carlos Alcaraz",
        scheduled_start_at=now,
        first_seen_at=now - timedelta(minutes=10),
        last_seen_at=now - timedelta(seconds=event_age_sec),
        status=status,
        is_live=is_live,
    )
    session.add(event)
    session.flush()

    market = Market(
        event_id=event.id,
        source_market_id=f"mkt_{uuid.uuid4().hex[:8]}",
        market_type="match_winner",
        name="match_winner",
        status="active",
    )
    session.add(market)
    session.flush()

    sel_a = MarketSelection(
        market_id=market.id,
        source_selection_id=f"sel_{uuid.uuid4().hex[:8]}",
        outcome="player_a",
        name="Daniil Medvedev",
        status="active",
    )
    session.add(sel_a)
    session.flush()

    odds_snap = OddsSnapshot(
        event_id=event.id,
        market_id=market.id,
        selection_id=sel_a.id,
        market_type="match_winner",
        outcome="player_a",
        odds=odds_val,
        probability_implied=1.0 / odds_val,
        status="active",
        is_live=is_live,
        observed_at=now - timedelta(seconds=odds_age_sec),
    )
    session.add(odds_snap)
    session.flush()

    return event, market, sel_a, odds_snap


def test_valid_bet_proposal_accepted():
    session = SessionLocal()
    try:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=50000.0)
        session.commit()

        event, market, sel_a, odds_snap = setup_test_fixtures(session)
        session.commit()

        proposal_id = str(uuid.uuid4())
        proposal = {
            "proposal_id": proposal_id,
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.10,
            "suggested_stake": 1500.0,
            "ml_prediction_id": str(uuid.uuid4()),
        }

        executor = SimulationExecutor()
        res = executor.process_proposal(session, proposal, account_name=acc_name)

        assert res.is_accepted is True, f"Expected proposal to be accepted, failed checks: {res.checks_failed}"
        assert res.rejection_code is None
        assert res.bet_id is not None
        assert "SCHEMA_VALIDATION" in res.checks_passed
        assert "ODDS_FRESHNESS" in res.checks_passed
        assert "BANKROLL_SUFFICIENCY" in res.checks_passed

        # Check DB audit trail
        val_db = session.query(DBBetValidationResult).filter(
            DBBetValidationResult.proposal_id == uuid.UUID(proposal_id)
        ).one()
        assert val_db.is_accepted is True
        assert val_db.rejection_code is None

        # Check virtual account balance updated
        updated_acc = session.get(VirtualAccount, account.id)
        assert float(updated_acc.balance) == 50000.0 - 1500.0
        assert float(updated_acc.locked_exposure) == 1500.0
        print("✓ test_valid_bet_proposal_accepted PASSED")
    finally:
        session.close()


def test_event_missing_rejected():
    session = SessionLocal()
    try:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=50000.0)
        session.commit()

        proposal = {
            "proposal_id": str(uuid.uuid4()),
            "event_id": str(uuid.uuid4()),  # non-existent
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(uuid.uuid4()),
            "bookmaker_odds": 2.00,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.10,
            "suggested_stake": 1000.0,
        }

        executor = SimulationExecutor()
        res = executor.process_proposal(session, proposal, account_name=acc_name)

        assert res.is_accepted is False
        assert res.rejection_code == reject_codes.EVENT_NOT_FOUND
        assert "EVENT_EXISTS" in res.checks_failed
        print("✓ test_event_missing_rejected PASSED")
    finally:
        session.close()


def test_event_finished_rejected():
    session = SessionLocal()
    try:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=50000.0)
        session.commit()

        event, market, sel_a, _ = setup_test_fixtures(session, status="finished", is_live=False)
        session.commit()

        proposal = {
            "proposal_id": str(uuid.uuid4()),
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.10,
            "suggested_stake": 1000.0,
        }

        executor = SimulationExecutor()
        res = executor.process_proposal(session, proposal, account_name=acc_name)

        assert res.is_accepted is False
        assert res.rejection_code == reject_codes.EVENT_FINISHED
        assert "EVENT_ACTIVE" in res.checks_failed
        print("✓ test_event_finished_rejected PASSED")
    finally:
        session.close()


def test_event_stale_rejected():
    session = SessionLocal()
    try:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=50000.0)
        session.commit()

        # event updated 45s ago (> 30s)
        event, market, sel_a, _ = setup_test_fixtures(session, event_age_sec=45.0)
        session.commit()

        proposal = {
            "proposal_id": str(uuid.uuid4()),
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.10,
            "suggested_stake": 1000.0,
        }

        executor = SimulationExecutor()
        res = executor.process_proposal(session, proposal, account_name=acc_name)

        assert res.is_accepted is False
        assert res.rejection_code == reject_codes.STALE_EVENT_STATE
        assert "EVENT_FRESHNESS" in res.checks_failed
        print("✓ test_event_stale_rejected PASSED")
    finally:
        session.close()


def test_market_suspended_rejected():
    session = SessionLocal()
    try:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=50000.0)
        session.commit()

        event, market, sel_a, _ = setup_test_fixtures(session)
        market.status = "suspended"
        session.commit()

        proposal = {
            "proposal_id": str(uuid.uuid4()),
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.10,
            "suggested_stake": 1000.0,
        }

        executor = SimulationExecutor()
        res = executor.process_proposal(session, proposal, account_name=acc_name)

        assert res.is_accepted is False
        assert res.rejection_code == reject_codes.MARKET_SUSPENDED
        assert "MARKET_ACTIVE" in res.checks_failed
        print("✓ test_market_suspended_rejected PASSED")
    finally:
        session.close()


def test_stale_odds_rejected():
    session = SessionLocal()
    try:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=50000.0)
        session.commit()

        # Odds updated 25s ago (> 15s)
        event, market, sel_a, _ = setup_test_fixtures(session, odds_age_sec=25.0)
        session.commit()

        proposal = {
            "proposal_id": str(uuid.uuid4()),
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.10,
            "suggested_stake": 1000.0,
        }

        executor = SimulationExecutor()
        res = executor.process_proposal(session, proposal, account_name=acc_name)

        assert res.is_accepted is False
        assert res.rejection_code == reject_codes.STALE_ODDS
        assert "ODDS_FRESHNESS" in res.checks_failed
        print("✓ test_stale_odds_rejected PASSED")
    finally:
        session.close()


def test_slippage_and_negative_movement_rejected():
    session = SessionLocal()
    try:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=50000.0)
        session.commit()

        # Bookmaker dropped odds drastically to 1.40 -> effective edge with prob 0.55 becomes 0.55 * 1.40 - 1 = -0.23 < 0.03
        event, market, sel_a, _ = setup_test_fixtures(session, odds_val=1.40)
        session.commit()

        proposal = {
            "proposal_id": str(uuid.uuid4()),
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.10,
            "suggested_stake": 1000.0,
        }

        executor = SimulationExecutor()
        res = executor.process_proposal(session, proposal, account_name=acc_name)

        assert res.is_accepted is False
        assert res.rejection_code == reject_codes.PRICE_SLIPPED
        assert "SLIPPAGE_EDGE" in res.checks_failed
        print("✓ test_slippage_and_negative_movement_rejected PASSED")
    finally:
        session.close()


def test_duplicate_proposal_rejected():
    session = SessionLocal()
    try:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=50000.0)
        session.commit()

        event, market, sel_a, _ = setup_test_fixtures(session)
        session.commit()

        proposal_1 = {
            "proposal_id": str(uuid.uuid4()),
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.10,
            "suggested_stake": 1000.0,
        }

        executor = SimulationExecutor()
        res1 = executor.process_proposal(session, proposal_1, account_name=acc_name)
        assert res1.is_accepted is True

        # Second proposal on same selection while first is PENDING
        proposal_2 = {
            "proposal_id": str(uuid.uuid4()),
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.10,
            "suggested_stake": 1000.0,
        }
        res2 = executor.process_proposal(session, proposal_2, account_name=acc_name)

        assert res2.is_accepted is False
        assert res2.rejection_code == reject_codes.DUPLICATE_PROPOSAL
        assert "DUPLICATE_CHECK" in res2.checks_failed
        print("✓ test_duplicate_proposal_rejected PASSED")
    finally:
        session.close()


def test_statistical_hurdles_rejected():
    session = SessionLocal()
    try:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=50000.0)
        session.commit()

        event, market, sel_a, _ = setup_test_fixtures(session)
        session.commit()

        executor = SimulationExecutor()

        # Confidence too low (< 0.50)
        p_low_conf = {
            "proposal_id": str(uuid.uuid4()),
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.45,
            "edge": 0.10,
            "suggested_stake": 1000.0,
        }
        res_conf = executor.process_proposal(session, p_low_conf, account_name=acc_name)
        assert res_conf.is_accepted is False
        assert res_conf.rejection_code == reject_codes.CONFIDENCE_TOO_LOW

        # Edge too low (< 0.03)
        p_low_edge = {
            "proposal_id": str(uuid.uuid4()),
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.01,
            "suggested_stake": 1000.0,
        }
        res_edge = executor.process_proposal(session, p_low_edge, account_name=acc_name)
        assert res_edge.is_accepted is False
        assert res_edge.rejection_code == reject_codes.EDGE_TOO_LOW
        print("✓ test_statistical_hurdles_rejected PASSED")
    finally:
        session.close()


def test_stake_limits_rejected():
    session = SessionLocal()
    try:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=50000.0)
        session.commit()

        event, market, sel_a, _ = setup_test_fixtures(session)
        session.commit()

        executor = SimulationExecutor()

        # Stake too low (< 100)
        p_low = {
            "proposal_id": str(uuid.uuid4()),
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.10,
            "suggested_stake": 50.0,
        }
        res_low = executor.process_proposal(session, p_low, account_name=acc_name)
        assert res_low.is_accepted is False
        assert res_low.rejection_code == reject_codes.STAKE_TOO_LOW

        # Stake exceeds max limit (> 10000)
        p_high = {
            "proposal_id": str(uuid.uuid4()),
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.10,
            "suggested_stake": 15000.0,
        }
        res_high = executor.process_proposal(session, p_high, account_name=acc_name)
        assert res_high.is_accepted is False
        assert res_high.rejection_code == reject_codes.STAKE_LIMIT_EXCEEDED
        print("✓ test_stake_limits_rejected PASSED")
    finally:
        session.close()


def test_insufficient_bankroll_rejected():
    session = SessionLocal()
    try:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=500.0)
        session.commit()

        event, market, sel_a, _ = setup_test_fixtures(session)
        session.commit()

        proposal = {
            "proposal_id": str(uuid.uuid4()),
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.10,
            "suggested_stake": 1000.0,  # balance is only 500
        }

        executor = SimulationExecutor()
        res = executor.process_proposal(session, proposal, account_name=acc_name)

        assert res.is_accepted is False
        assert res.rejection_code == reject_codes.INSUFFICIENT_BANKROLL
        assert "BANKROLL_SUFFICIENCY" in res.checks_failed
        print("✓ test_insufficient_bankroll_rejected PASSED")
    finally:
        session.close()


def test_exposure_limit_rejected():
    session = SessionLocal()
    try:
        # Bankroll 100,000 -> max 20% exposure is 20,000
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=100000.0)
        # Lock 18,000 already
        account.locked_exposure = 18000.0
        session.commit()

        event, market, sel_a, _ = setup_test_fixtures(session)
        session.commit()

        # Staking 5,000 would bring exposure to 23,000 > 20,000
        proposal = {
            "proposal_id": str(uuid.uuid4()),
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.10,
            "suggested_stake": 5000.0,
        }

        executor = SimulationExecutor()
        res = executor.process_proposal(session, proposal, account_name=acc_name)

        assert res.is_accepted is False
        assert res.rejection_code == reject_codes.EXPOSURE_LIMIT
        assert "EXPOSURE_LIMIT" in res.checks_failed
        print("✓ test_exposure_limit_rejected PASSED")
    finally:
        session.close()


def test_unsupported_sport_rejected():
    session = SessionLocal()
    try:
        acc_name = f"test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=50000.0)
        session.commit()

        event, market, sel_a, _ = setup_test_fixtures(session)
        session.commit()

        proposal = {
            "proposal_id": str(uuid.uuid4()),
            "event_id": str(event.id),
            "sport_code": "curling",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.10,
            "suggested_stake": 1000.0,
        }

        executor = SimulationExecutor()
        res = executor.process_proposal(session, proposal, account_name=acc_name)

        assert res.is_accepted is False
        assert res.rejection_code == reject_codes.UNSUPPORTED_SPORT
        assert "SPORT_SUPPORT" in res.checks_failed
        print("✓ test_unsupported_sport_rejected PASSED")
    finally:
        session.close()


def test_http_api_endpoints():
    session = SessionLocal()
    try:
        acc_name = f"http_test_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=50000.0)
        event, market, sel_a, _ = setup_test_fixtures(session)
        session.commit()

        client = TestClient(app)

        # 1. Healthcheck
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "bet-manager"
        assert "tennis" in data["supported_sports"]

        # 2. Evaluate Proposal
        proposal_id = str(uuid.uuid4())
        payload = {
            "proposal_id": proposal_id,
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": "match_winner",
            "outcome": "player_a",
            "selection_id": str(sel_a.id),
            "bookmaker_odds": 2.20,
            "fair_odds": 1.80,
            "model_probability": 0.55,
            "edge": 0.10,
            "suggested_stake": 1200.0,
        }
        eval_resp = client.post(f"/api/proposals/evaluate?account_name={acc_name}", json=payload)
        assert eval_resp.status_code == 200
        eval_data = eval_resp.json()
        assert eval_data["is_accepted"] is True
        assert eval_data["bet_id"] is not None

        # 3. Retrieve validation audit trail
        val_resp = client.get(f"/api/proposals/{proposal_id}/validation")
        assert val_resp.status_code == 200
        val_data = val_resp.json()
        assert val_data["is_accepted"] is True
        assert len(val_data["checks_passed"]) > 10

        # 4. Bankroll summary
        bank_resp = client.get(f"/api/bankroll?account_name={acc_name}")
        assert bank_resp.status_code == 200
        bank_data = bank_resp.json()
        assert bank_data["available_balance"] == 50000.0 - 1200.0
        assert bank_data["locked_exposure"] == 1200.0
        assert bank_data["is_balanced"] is True
        print("✓ test_http_api_endpoints PASSED")
    finally:
        session.close()


if __name__ == "__main__":
    print("\n--- Running Bet Manager Validation & Risk Tests ---")
    test_valid_bet_proposal_accepted()
    test_event_missing_rejected()
    test_event_finished_rejected()
    test_event_stale_rejected()
    test_market_suspended_rejected()
    test_stale_odds_rejected()
    test_slippage_and_negative_movement_rejected()
    test_duplicate_proposal_rejected()
    test_statistical_hurdles_rejected()
    test_stake_limits_rejected()
    test_insufficient_bankroll_rejected()
    test_exposure_limit_rejected()
    test_unsupported_sport_rejected()
    test_http_api_endpoints()
    print("\nALL BET MANAGER TESTS PASSED SUCCESSFULLY!")
