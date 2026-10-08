from datetime import datetime, timezone
from pydantic import ValidationError

from contracts import (
    Event,
    EventState,
    TennisSetScore,
    TennisGameScore,
    Market,
    Selection,
    OddsSnapshot,
    OutcomePrediction,
    MLPrediction,
    LLMDecision,
    BetProposal,
    BetValidationResult,
    VirtualBet,
    Settlement,
)


def test_event_contract():
    now = datetime.now(timezone.utc)
    event = Event(
        event_id="tennis_evt_123",
        source="fonbet",
        source_event_id="fb_9981",
        sport_code="tennis",
        tournament="Wimbledon 2026 - Men's Singles",
        participant_a="Carlos Alcaraz",
        participant_b="Jannik Sinner",
        scheduled_start=now,
        is_live=True,
        status="live",
        metadata={"surface": "grass"},
    )
    assert event.schema_version == "1.0.0"
    assert event.sport_code == "tennis"
    assert event.is_live is True
    print("test_event_contract: OK")


def test_tennis_event_state():
    state = EventState(
        event_id="tennis_evt_123",
        sport_code="tennis",
        current_period=2,
        sets=[
            TennisSetScore(set_number=1, games_a=6, games_b=4),
            TennisSetScore(set_number=2, games_a=3, games_b=2),
        ],
        current_game=TennisGameScore(points_a="40", points_b="30", is_tiebreak=False),
        server="player_a",
        is_break_point=False,
        stats={"aces_a": 5, "aces_b": 4, "double_faults_a": 1, "double_faults_b": 2},
    )
    assert state.server == "player_a"
    assert state.sets[0].games_a == 6
    assert state.current_game.points_a == "40"
    print("test_tennis_event_state: OK")


def test_odds_and_tennis_value_prediction():
    sel_a = Selection(
        selection_id="sel_1",
        outcome="player_a",
        name="Carlos Alcaraz",
        odds=1.55,
        probability_implied=round(1.0 / 1.55, 4),
    )
    sel_b = Selection(
        selection_id="sel_2",
        outcome="player_b",
        name="Jannik Sinner",
        odds=2.45,
        probability_implied=round(1.0 / 2.45, 4),
    )
    market = Market(
        market_id="mkt_match_winner",
        market_type="match_winner",
        name="Match Winner",
        selections=[sel_a, sel_b],
    )
    snapshot = OddsSnapshot(
        snapshot_id="snap_101",
        event_id="tennis_evt_123",
        markets=[market],
        content_hash="sha256:abc123hash",
    )
    assert len(snapshot.markets) == 1

    # User's exact example:
    # model_probability: 0.70, bookmaker_odds: 1.55, fair_odds: 1.43, edge: 0.085, expected_value: 0.085
    outcome_pred = OutcomePrediction(
        outcome="player_a",
        model_probability=0.70,
        bookmaker_odds=1.55,
        fair_odds=round(1.0 / 0.70, 2),
        edge=round((0.70 * 1.55) - 1.0, 3),
        expected_value=round((0.70 * (1.55 - 1.0)) - (0.30 * 1.0), 3),
        confidence=0.85,
    )
    assert outcome_pred.edge > 0.08
    assert outcome_pred.expected_value > 0.08

    ml_pred = MLPrediction(
        prediction_id="pred_999",
        event_id="tennis_evt_123",
        sport_code="tennis",
        model_version="tennis_lgbm_v1.0",
        market="match_winner",
        outcomes=[outcome_pred],
        best_outcome="player_a",
        has_positive_edge=True,
    )
    assert ml_pred.has_positive_edge is True
    assert ml_pred.schema_version == "1.0.0"
    print("test_odds_and_tennis_value_prediction: OK")


def test_llm_decision_rejection_on_invalid_data():
    failed = False
    try:
        LLMDecision(
            decision_id="dec_1",
            event_id="tennis_evt_123",
            llm_version="qwen2.5:3b",
            confidence_adjustment=0.99,  # Should fail ge=-0.25, le=0.25 constraint
            reasoning="Invalid test",
        )
    except ValidationError:
        failed = True
    assert failed is True
    print("test_llm_decision_rejection_on_invalid_data: OK")


if __name__ == "__main__":
    test_event_contract()
    test_tennis_event_state()
    test_odds_and_tennis_value_prediction()
    test_llm_decision_rejection_on_invalid_data()
    print("ALL TESTS PASSED SUCCESSFULLY!")
