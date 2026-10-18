"""Confirmed ACTIVE tennis proposals reach the ledger. Shadow and repeat calls do not."""

from datetime import datetime, timezone, timedelta
import uuid

from db.connection import SessionLocal
from db.models.sports import Sport
from db.models.events import Event
from db.models.odds import Market, MarketSelection, OddsSnapshot
from db.models.ml_registry import ModelVersion
from db.models.predictions import MLPrediction
from db.models.betting import Bet, LedgerEntry
from bankroll.service import BankrollService

from app.executor import SimulationExecutor
from app import reject_codes


def setup_test_fixtures(session, odds_val=1.80):
    sport = session.get(Sport, "tennis")
    if not sport:
        sport = Sport(code="tennis", name="Tennis", is_primary=True)
        session.add(sport)
        session.flush()
    now = datetime.now(timezone.utc)
    event = Event(
        source="fonbet",
        source_event_id=f"evt_auto_{uuid.uuid4().hex[:8]}",
        sport_code="tennis",
        participant_a_name="Daniil Medvedev",
        participant_b_name="Carlos Alcaraz",
        scheduled_start_at=now,
        first_seen_at=now - timedelta(minutes=10),
        last_seen_at=now - timedelta(seconds=5),
        status="live",
        is_live=True,
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
    selection = MarketSelection(
        market_id=market.id,
        source_selection_id=f"sel_{uuid.uuid4().hex[:8]}",
        outcome="player_a",
        name="Daniil Medvedev",
        status="active",
    )
    session.add(selection)
    session.flush()
    session.add(
        OddsSnapshot(
            event_id=event.id,
            market_id=market.id,
            selection_id=selection.id,
            market_type="match_winner",
            outcome="player_a",
            odds=odds_val,
            probability_implied=1.0 / odds_val,
            status="active",
            is_live=True,
            observed_at=now - timedelta(seconds=5),
        )
    )
    session.flush()
    return event, market, selection


def _proposal(event, selection, **extra):
    payload = {
        "proposal_id": str(uuid.uuid4()),
        "event_id": str(event.id),
        "sport_code": "tennis",
        "market": "match_winner",
        "outcome": "player_a",
        "selection_id": str(selection.id),
        "bookmaker_odds": 1.80,
        "fair_odds": 1.43,
        "model_probability": 0.70,
        "edge": 0.26,
        "suggested_stake": 400.0,
        "enforce_sport_policy": True,
        "model_name": extra.get("model_name", "test_auto_stake"),
        "model_version": extra.get("model_version", "test_tennis_v9.9.9"),
        "data_quality_score": 90,
    }
    payload.update(extra)
    return payload


def test_active_policy_places_one_virtual_bet():
    session = SessionLocal()
    version_id = uuid.uuid4()
    try:
        account_name = f"auto_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=account_name, initial_balance=10_000.0)
        event, _market, selection = setup_test_fixtures(session, odds_val=1.80)
        version = ModelVersion(
            id=version_id,
            model_name="test_auto_stake",
            version_tag="test_tennis_v9.9.9",
            sport_code="tennis",
            market="match_winner",
            artifact_path="/tmp/test_auto_stake.joblib",
            metrics={},
            hyperparameters={},
            status="ACTIVE",
        )
        prediction = MLPrediction(
            event_id=event.id,
            sport_code="tennis",
            model_version="test_tennis_v9.9.9",
            market="match_winner",
            predicted_outcome="player_a",
            model_probability=0.70,
            bookmaker_odds=1.80,
            fair_odds=1.43,
            edge=0.26,
            expected_value=0.26,
            confidence=0.80,
            has_positive_edge=True,
            is_shadow=False,
            outcomes_detail={"model_name": "test_auto_stake"},
            created_at=datetime.now(timezone.utc),
        )
        session.add(version)
        session.add(prediction)
        session.commit()

        payload = _proposal(event, selection, ml_prediction_id=str(prediction.id))
        result = SimulationExecutor().process_proposal(session, payload, account_name=account_name)
        assert result.is_accepted is True, result.rejection_code
        assert result.bet_id is not None
        again = SimulationExecutor().process_proposal(session, payload, account_name=account_name)
        assert again.is_accepted is True
        assert again.bet_id == result.bet_id
        bets = session.query(Bet).filter(Bet.proposal_id == uuid.UUID(payload["proposal_id"])).all()
        assert len(bets) == 1
        assert float(bets[0].stake) == 400.0
        placed = session.query(LedgerEntry).filter(LedgerEntry.bet_id == bets[0].id, LedgerEntry.entry_type == "BET_PLACED").count()
        assert placed == 1
        print("active policy places one bet")
    finally:
        session.rollback()
        session.close()
        with SessionLocal() as cleanup:
            row = cleanup.get(ModelVersion, version_id)
            if row is not None:
                cleanup.delete(row)
                cleanup.commit()


def test_shadow_prediction_does_not_move_the_bank():
    session = SessionLocal()
    try:
        account_name = f"shadow_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=account_name, initial_balance=10_000.0)
        event, _market, selection = setup_test_fixtures(session, odds_val=1.80)
        prediction = MLPrediction(
            event_id=event.id,
            sport_code="tennis",
            model_version="test_tennis_v9.9.8",
            market="match_winner",
            predicted_outcome="player_a",
            model_probability=0.70,
            bookmaker_odds=1.80,
            fair_odds=1.43,
            edge=0.26,
            expected_value=0.26,
            confidence=0.80,
            has_positive_edge=True,
            is_shadow=True,
            outcomes_detail={"model_name": "test_auto_stake"},
            created_at=datetime.now(timezone.utc),
        )
        session.add(prediction)
        session.commit()
        before = float(account.balance)
        payload = _proposal(event, selection, ml_prediction_id=str(prediction.id))
        result = SimulationExecutor().process_proposal(session, payload, account_name=account_name)
        assert result.is_accepted is False
        assert result.rejection_code == reject_codes.SHADOW_MODEL
        session.refresh(account)
        assert float(account.balance) == before
        print("shadow prediction stays off the ledger")
    finally:
        session.close()


if __name__ == "__main__":
    test_active_policy_places_one_virtual_bet()
    test_shadow_prediction_does_not_move_the_bank()
    print("auto execution ok")
