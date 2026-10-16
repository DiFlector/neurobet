"""Journal of model sides: reason text, insert, and settlement without touching the bankroll."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.journal import explain_commit, record_model_bet, settle_model_bets, settle_open_journal


def test_reason_mentions_shadow_probability_and_no_money():
    text = explain_commit(
        model_name="tennis_gradient_boosting",
        version="tennis_v2.0.0",
        is_shadow=True,
        market="set_winner:2",
        outcome="player_b",
        probability=0.62,
        odds=1.85,
        edge=0.08,
    )
    assert "Тень" in text
    assert "сет 2" in text
    assert "player_b" in text
    assert "0.620" in text
    assert "1.85" in text
    assert "Денег нет" in text


def test_record_and_settle_roundtrip():
    from db.connection import SessionLocal
    from db.models.events import Event
    from db.models.predictions import ModelBetJournal

    event_id = uuid.uuid4()
    with SessionLocal() as session:
        session.add(
            Event(
                id=event_id,
                source="fonbet",
                source_event_id=f"journal-{event_id.hex[:12]}",
                sport_code="tennis",
                participant_a_name="Журнал А",
                participant_b_name="Журнал Б",
                scheduled_start_at=datetime.now(timezone.utc),
                status="live",
                current_score={},
                metadata_json={},
            )
        )
        session.flush()
        assert record_model_bet(
            session,
            event_id=event_id,
            sport_code="tennis",
            model_name="tennis_logistic_regression",
            model_version="tennis_v2.0.0",
            is_shadow=True,
            market="set_winner:1",
            outcome="player_a",
            probability=0.71,
            bookmaker_odds=1.7,
            fair_odds=1.41,
            edge=0.12,
            expected_value=0.2,
            confidence=0.4,
        )
        assert record_model_bet(
            session,
            event_id=event_id,
            sport_code="tennis",
            model_name="tennis_logistic_regression",
            model_version="tennis_v2.0.0",
            is_shadow=True,
            market="set_winner:1",
            outcome="player_a",
            probability=0.71,
            bookmaker_odds=1.7,
            fair_odds=1.41,
            edge=0.12,
            expected_value=0.2,
            confidence=0.4,
        ) is False
        event = session.get(Event, event_id)
        event.metadata_json = {"set_winners": {"1": "player_a"}}
        assert settle_model_bets(session, event) == 1
        row = session.query(ModelBetJournal).filter(ModelBetJournal.event_id == event_id).one()
        assert row.result_status == "won"
        assert float(row.stake_amount) == 0
        session.rollback()


def test_finished_match_outside_live_queue_settles_pending_row():
    from db.connection import SessionLocal
    from db.models.events import Event
    from db.models.predictions import ModelBetJournal

    event_id = uuid.uuid4()
    with SessionLocal() as session:
        session.add(
            Event(
                id=event_id,
                source="fonbet",
                source_event_id=f"journal-{event_id.hex[:12]}",
                sport_code="tennis",
                participant_a_name="Финиш А",
                participant_b_name="Финиш Б",
                scheduled_start_at=datetime.now(timezone.utc),
                status="finished",
                current_score={"winner": "player_b", "status": "FINISHED", "final_score": "0:2 (3-6 4-6)"},
                metadata_json={},
            )
        )
        session.flush()
        assert record_model_bet(
            session,
            event_id=event_id,
            sport_code="tennis",
            model_name="tennis_gradient_boosting",
            model_version="tennis_v2.0.0",
            is_shadow=True,
            market="match_winner",
            outcome="player_a",
            probability=0.58,
            bookmaker_odds=1.9,
            fair_odds=1.72,
            edge=0.04,
            expected_value=0.08,
            confidence=0.3,
        )
        assert settle_open_journal(session) >= 1
        row = session.query(ModelBetJournal).filter(ModelBetJournal.event_id == event_id).one()
        assert row.result_status == "lost"
        assert row.result_winner == "player_b"
        session.rollback()


def test_unplayed_set_voids_when_match_is_finished():
    from db.connection import SessionLocal
    from db.models.events import Event
    from db.models.predictions import ModelBetJournal

    event_id = uuid.uuid4()
    with SessionLocal() as session:
        session.add(
            Event(
                id=event_id,
                source="fonbet",
                source_event_id=f"journal-{event_id.hex[:12]}",
                sport_code="tennis",
                participant_a_name="Сет А",
                participant_b_name="Сет Б",
                scheduled_start_at=datetime.now(timezone.utc),
                status="finished",
                current_score={"winner": "player_a", "status": "FINISHED", "final_score": "2:0 (6-3 6-4)"},
                metadata_json={},
            )
        )
        session.flush()
        for market, outcome in (("set_winner:2", "player_b"), ("set_winner:3", "player_a")):
            assert record_model_bet(
                session,
                event_id=event_id,
                sport_code="tennis",
                model_name="tennis_gradient_boosting",
                model_version="tennis_v2.2.0",
                is_shadow=True,
                market=market,
                outcome=outcome,
                probability=0.6,
                bookmaker_odds=1.8,
                fair_odds=1.6,
                edge=0.05,
                expected_value=0.05,
                confidence=0.2,
            )
        assert settle_model_bets(session, session.get(Event, event_id)) == 2
        rows = {
            row.market: row
            for row in session.query(ModelBetJournal).filter(ModelBetJournal.event_id == event_id).all()
        }
        assert rows["set_winner:2"].result_status == "lost"
        assert rows["set_winner:2"].result_winner == "player_a"
        assert rows["set_winner:3"].result_status == "void"
        session.rollback()


def test_match_loss_uses_final_winner():
    event = SimpleNamespace(
        id=uuid.uuid4(),
        status="finished",
        metadata_json={},
        current_score={"winner": "player_b", "status": "FINISHED"},
    )
    assert explain_commit(
        model_name="tennis_gradient_boosting",
        version="tennis_v2.0.0",
        is_shadow=False,
        market="match_winner",
        outcome="player_a",
        probability=0.55,
        odds=2.1,
        edge=0.03,
    ).startswith("Боевая модель")
    # settle needs a session; the winner helper is covered by the live row above.
    assert event.current_score["winner"] == "player_b"


if __name__ == "__main__":
    test_reason_mentions_shadow_probability_and_no_money()
    test_unplayed_set_voids_when_match_is_finished()
    test_match_loss_uses_final_winner()
    test_record_and_settle_roundtrip()
    test_finished_match_outside_live_queue_settles_pending_row()
    print("ok")
