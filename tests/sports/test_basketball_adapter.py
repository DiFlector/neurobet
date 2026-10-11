"""Automated test suite for Phase 26: Basketball Sport Adapter, state schema, feature builder, and isolation."""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

# Ensure package paths are present
paths = [
    "/srv/neurobet/packages/contracts/src",
    "/srv/neurobet/packages/features/src",
    "/srv/neurobet/packages/db/src",
    "/srv/neurobet/packages/sports-core/src",
    "/srv/neurobet/packages/bankroll/src",
    "/srv/neurobet/sports/tennis/src",
    "/srv/neurobet/sports/basketball/src",
    "/srv/neurobet/services/neural",
    "/srv/neurobet/services/backend",
    "/app/packages/contracts/src",
    "/app/packages/features/src",
    "/app/packages/db/src",
    "/app/packages/sports-core/src",
    "/app/packages/bankroll/src",
    "/app/sports/tennis/src",
    "/app/sports/basketball/src",
    "/app",
]
for p in paths:
    if p not in sys.path:
        sys.path.insert(0, p)

from sports_core import registry, FonbetResultsParser
from tennis_adapter import TennisSportAdapter, detect_tennis_surface, normalize_russian_player_name
from basketball_adapter import (
    BasketballSportAdapter,
    BasketballPipeline,
    normalize_russian_basketball_team,
    is_basketball_enabled,
    register_basketball_adapter,
    unregister_basketball_adapter,
)
from features import BasketballFeatureBuilder, TennisFeatureBuilder, registry as feature_registry
from contracts import (
    Event,
    EventState,
    BasketballQuarterScore,
    FeatureVector,
    MLPrediction,
)
from unittest.mock import MagicMock


def test_config_flag_gating():
    """Verify Basketball is gated strictly by configuration flag."""
    # 1. When unregistered / disabled
    unregister_basketball_adapter()
    assert registry.is_supported("basketball") is False
    assert "basketball" not in registry.list_sports()

    desc_unsupported = registry.get_descriptor("basketball")
    assert desc_unsupported.supported is False
    assert desc_unsupported.status == "UNSUPPORTED"
    assert desc_unsupported.participant_type == "team"

    # 2. When registered / enabled
    register_basketball_adapter()
    assert registry.is_supported("basketball") is True
    assert "basketball" in registry.list_sports()

    desc_active = registry.get_descriptor("basketball")
    assert desc_active.supported is True
    assert desc_active.status == "ACTIVE"
    assert desc_active.name_ru == "Баскетбол"
    assert desc_active.participant_type == "team"
    market_types = [m.market_type for m in desc_active.supported_markets]
    assert "match_winner" in market_types
    assert "handicap_points" in market_types
    assert "total_points" in market_types
    assert "quarter_winner" in market_types
    print("✓ test_config_flag_gating passed")


def test_russian_team_name_normalization():
    """Verify Russian team normalization for VTB, EuroLeague, and NBA."""
    # VTB United League
    assert normalize_russian_basketball_team("ЦСКА") == "ЦСКА Москва"
    assert normalize_russian_basketball_team("ЦСКА Москва") == "ЦСКА Москва"
    assert normalize_russian_basketball_team("ПБК ЦСКА") == "ЦСКА Москва"
    assert normalize_russian_basketball_team("[1] Зенит Санкт-Петербург") == "Зенит Санкт-Петербург"
    assert normalize_russian_basketball_team("УНИКС") == "УНИКС Казань"
    assert normalize_russian_basketball_team("Локо-Кубань") == "Локомотив-Кубань"
    assert normalize_russian_basketball_team("Парма") == "Парма Пермь"
    assert normalize_russian_basketball_team("Пари Нижний Новгород") == "Пари Нижний Новгород"

    # EuroLeague
    assert normalize_russian_basketball_team("Реал Мадрид") == "Реал Мадрид"
    assert normalize_russian_basketball_team("Барселона (рез)") == "Барселона"
    assert normalize_russian_basketball_team("Фенербахче") == "Фенербахче"
    assert normalize_russian_basketball_team("Олимпиакос") == "Олимпиакос"
    assert normalize_russian_basketball_team("Панатинаикос") == "Панатинаикос"

    # NBA
    assert normalize_russian_basketball_team("Бостон") == "Бостон Селтикс"
    assert normalize_russian_basketball_team("ЛА Лейкерс") == "Лос-Анджелес Лейкерс"
    assert normalize_russian_basketball_team("Голден Стэйт") == "Голден Стэйт Уорриорз"
    assert normalize_russian_basketball_team("Денвер Наггетс") == "Денвер Наггетс"
    print("✓ test_russian_team_name_normalization passed")


def test_hierarchical_quarters_and_state_parsing():
    """Verify basketball quarters parsing (match -> quarter -> score/clock/possession)."""
    adapter = BasketballSportAdapter()

    raw_payload = {
        "id": "1948201",
        "competitionName": "Баскетбол. Единая лига ВТБ",
        "team1": "ЦСКА",
        "team2": "Зенит",
        "score": "84:79 (22-18, 19-24, 21-16, 22-21)",
        "current_period": 4,
        "match_clock_seconds": 540,
        "possession": "home",
        "fouls_a": 4,
        "fouls_b": 5,
        "timeouts_a": 1,
        "timeouts_b": 2,
    }

    # 1. Parse Event
    event = adapter.parse_event(raw_payload)
    assert event.sport_code == "basketball"
    assert event.participant_a == "ЦСКА Москва"
    assert event.participant_b == "Зенит Санкт-Петербург"
    assert event.is_live is True

    # 2. Parse State
    state = adapter.parse_state(raw_payload)
    assert state.sport_code == "basketball"
    assert len(state.quarters) == 4
    assert state.quarters[0].points_a == 22 and state.quarters[0].points_b == 18
    assert state.quarters[1].points_a == 19 and state.quarters[1].points_b == 24
    assert state.quarters[2].points_a == 21 and state.quarters[2].points_b == 16
    assert state.quarters[3].points_a == 22 and state.quarters[3].points_b == 21
    assert state.current_period == 4
    assert state.possession == "team_a"
    assert state.match_clock_seconds == 540
    assert state.stats["fouls_a"] == 4
    assert state.stats["timeouts_b"] == 2
    print("✓ test_hierarchical_quarters_and_state_parsing passed")


def test_basketball_odds_and_markets():
    """Verify basketball odds snapshotting: moneyline, handicap, total points."""
    adapter = BasketballSportAdapter()

    raw_odds_payload = {
        "event_id": "bb_101",
        "markets": [
            {
                "market_type": "match_winner",
                "name": "Победитель матча",
                "selections": [
                    {"outcome": "team_a", "odds": 1.65},
                    {"outcome": "team_b", "odds": 2.25},
                ],
            },
            {
                "market_type": "handicap_points",
                "name": "Фора",
                "line_value": -3.5,
                "selections": [
                    {"outcome": "team_a", "odds": 1.90},
                    {"outcome": "team_b", "odds": 1.90},
                ],
            },
            {
                "market_type": "total_points",
                "name": "Тотал очков",
                "line_value": 163.5,
                "selections": [
                    {"outcome": "over", "odds": 1.88},
                    {"outcome": "under", "odds": 1.92},
                ],
            },
        ],
    }

    odds_snapshot = adapter.parse_odds(raw_odds_payload)
    assert odds_snapshot.event_id == "bb_101"
    assert odds_snapshot.snapshot_id is not None
    assert len(odds_snapshot.markets) == 3
    assert odds_snapshot.content_hash is not None

    mkt_types = [m.market_type for m in odds_snapshot.markets]
    assert "match_winner" in mkt_types
    assert "handicap_points" in mkt_types
    assert "total_points" in mkt_types

    # Rule validation
    assert adapter.validate_sport_rules({"market": "match_winner", "outcome": "team_a", "bookmaker_odds": 1.70}) is True
    assert adapter.validate_sport_rules({"market": "total_points", "outcome": "over", "bookmaker_odds": 1.90}) is True
    assert adapter.validate_sport_rules({"market": "invalid_market", "outcome": "team_a", "bookmaker_odds": 1.70}) is False
    assert adapter.validate_sport_rules({"market": "match_winner", "outcome": "team_a", "bookmaker_odds": 0.95}) is False
    print("✓ test_basketball_odds_and_markets passed")


def test_basketball_features_zero_future_leakage():
    """Verify Basketball feature builder guarantees zero future data leakage."""
    builder = BasketballFeatureBuilder()
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(minutes=15)
    t1 = now - timedelta(minutes=10)
    t2 = now - timedelta(minutes=5)
    t_future = now + timedelta(minutes=5)

    class MockSnapshot:
        def __init__(self, observed_at, **kwargs):
            self.observed_at = observed_at
            for k, v in kwargs.items():
                setattr(self, k, v)

    event_snapshots = [
        MockSnapshot(
            t0,
            current_period=1,
            match_clock_seconds=300,
            score_detail={"quarters_summary": [{"q": 1, "a": 20, "b": 18}], "fouls_a": 1, "fouls_b": 2},
        ),
        MockSnapshot(
            t1,
            current_period=2,
            match_clock_seconds=600,
            score_detail={"quarters_summary": [{"q": 1, "a": 20, "b": 18}, {"q": 2, "a": 22, "b": 20}], "fouls_a": 3, "fouls_b": 3},
        ),
        MockSnapshot(
            t2,
            current_period=3,
            match_clock_seconds=300,
            score_detail={"quarters_summary": [{"q": 1, "a": 20, "b": 18}, {"q": 2, "a": 22, "b": 20}, {"q": 3, "a": 24, "b": 18}], "fouls_a": 4, "fouls_b": 5},
        ),
        # Future state snapshot - MUST NOT BE LEAKED!
        MockSnapshot(
            t_future,
            current_period=4,
            match_clock_seconds=600,
            score_detail={"quarters_summary": [{"q": 1, "a": 20, "b": 18}, {"q": 2, "a": 22, "b": 20}, {"q": 3, "a": 24, "b": 18}, {"q": 4, "a": 25, "b": 22}], "fouls_a": 6, "fouls_b": 6},
        ),
    ]

    odds_snapshots = [
        MockSnapshot(t0, outcome="team_a", odds=1.85),
        MockSnapshot(t0, outcome="team_b", odds=1.95),
        MockSnapshot(t1, outcome="team_a", odds=1.75),
        MockSnapshot(t1, outcome="team_b", odds=2.10),
        MockSnapshot(t2, outcome="team_a", odds=1.60),
        MockSnapshot(t2, outcome="team_b", odds=2.35),
        # Future odds snapshot - MUST NOT BE LEAKED!
        MockSnapshot(t_future, outcome="team_a", odds=1.10),
        MockSnapshot(t_future, outcome="team_b", odds=7.00),
    ]

    fv = builder.build_features(
        event_id="bb_leak_test",
        cutoff_time=t2,
        event_snapshots=event_snapshots,
        odds_snapshots=odds_snapshots,
    )

    assert fv.sport_code == "basketball"
    # At cutoff t2 (Q3 end), total score is (20+22+24)=66 for A, (18+20+18)=56 for B
    assert fv.features["points_team_a"] == 66.0
    assert fv.features["points_team_b"] == 56.0
    assert fv.features["point_differential"] == 10.0
    assert fv.features["current_quarter"] == 3.0
    assert fv.features["current_odds_team_a"] == 1.60
    assert fv.features["current_odds_team_b"] == 2.35

    # Check future odds (1.10) were completely excluded
    assert fv.features["current_odds_team_a"] != 1.10

    # Implied pace feature is present and positive
    assert fv.features["implied_game_pace"] > 100.0
    print("✓ test_basketball_features_zero_future_leakage passed")


def test_basketball_settlement_and_results_parser():
    """Verify Fonbet basketball official results parsing and settlement."""
    # 1. Finished regular time
    w, status, is_canc, is_wo = FonbetResultsParser.parse_basketball_winner_and_status("84:78 (21-18, 19-20, 24-22, 20-18)")
    assert w == "team_a"
    assert status == "FINISHED"
    assert is_canc is False

    # 2. Finished overtime
    w_ot, status_ot, _, _ = FonbetResultsParser.parse_basketball_winner_and_status("89:93 ОТ (18-20, 22-20, 24-24, 18-18, 7-11)")
    assert w_ot == "team_b"
    assert status_ot == "FINISHED"

    # 3. Cancelled / Walkover
    w_canc, status_canc, is_canc, is_wo = FonbetResultsParser.parse_basketball_winner_and_status("-:-", "Матч отменен")
    assert w_canc is None
    assert status_canc == "CANCELLED"
    assert is_canc is True

    # 4. Adapter Settlement parsing
    adapter = BasketballSportAdapter()
    settle_win = adapter.parse_result({
        "event_id": "bb_settle_1",
        "status": "finished",
        "score": "95:90",
        "outcome": "team_a",
        "odds": 1.85,
    })
    assert settle_win is not None
    assert settle_win.status == "WON"
    assert settle_win.net_profit > 0.0

    settle_loss = adapter.parse_result({
        "event_id": "bb_settle_2",
        "status": "finished",
        "score": "80:88",
        "outcome": "team_a",
        "odds": 1.85,
    })
    assert settle_loss is not None
    assert settle_loss.status == "LOST"
    assert settle_loss.net_profit < 0.0
    print("✓ test_basketball_settlement_and_results_parser passed")


def test_basketball_baseline_ml_inference():
    try:
        from services.neural.app.inference import MLPredictor
    except ImportError:
        try:
            from app.inference import MLPredictor
        except ImportError:
            from neural.app.inference import MLPredictor

    # Create mock predictor
    predictor = MLPredictor()
    mock_model = MagicMock()
    mock_model.predict_proba.return_value = [[0.38, 0.62]]  # team_b=0.38, team_a=0.62

    predictor.checkpoint_data = {
        "model": mock_model,
        "feature_names": ["point_differential", "implied_game_pace", "no_vig_prob_team_a"],
        "version_tag": "basketball_v1.0",
        "market": "match_winner",
    }

    fv = FeatureVector(
        event_id="bb_inf_test",
        sport_code="basketball",
        feature_version="1.0.0",
        timestamp=datetime.now(timezone.utc),
        features={
            "point_differential": 6.0,
            "implied_game_pace": 165.0,
            "no_vig_prob_team_a": 0.58,
            "current_odds_team_a": 1.75,
            "current_odds_team_b": 2.10,
        },
    )

    pred = predictor.predict(fv)
    assert pred.sport_code == "basketball"
    assert pred.model_version == "basketball_v1.0"
    assert len(pred.outcomes) == 2
    assert pred.outcomes[0].outcome == "team_a"
    assert pred.outcomes[1].outcome == "team_b"
    assert pred.outcomes[0].model_probability == 0.62
    assert pred.outcomes[1].model_probability == 0.38
    assert pred.outcomes[0].fair_odds > 1.0
    assert pred.best_outcome == "team_a"
    assert pred.has_positive_edge is True  # 0.62 * 1.75 - 1 = 0.085 > 0.02
    print("✓ test_basketball_baseline_ml_inference passed")


def test_tennis_pipeline_isolation():
    """Verify Tennis pipeline remains 100% operational and isolated."""
    tennis_adapter = registry.get("tennis")
    assert tennis_adapter is not None
    assert tennis_adapter.sport_code == "tennis"
    assert "set_winner" in tennis_adapter.supported_markets()
    assert "total_games" in tennis_adapter.supported_markets()

    # Tennis surface and player normalization work seamlessly
    assert detect_tennis_surface("ATP. Мастерс 1000. Рим (Грунт)") == "clay"
    assert normalize_russian_player_name("Медведев Д.") == "Даниил Медведев"

    # Tennis feature builder registered and intact
    t_builder = feature_registry.get("tennis")
    assert t_builder is not None
    assert t_builder.sport_code == "tennis"
    print("✓ test_tennis_pipeline_isolation passed")


if __name__ == "__main__":
    print("Running Phase 26 Basketball Sport Test Suite...")
    test_config_flag_gating()
    test_russian_team_name_normalization()
    test_hierarchical_quarters_and_state_parsing()
    test_basketball_odds_and_markets()
    test_basketball_features_zero_future_leakage()
    test_basketball_settlement_and_results_parser()
    test_basketball_baseline_ml_inference()
    test_tennis_pipeline_isolation()
    print("\n🎉 ALL PHASE 26 BASKETBALL SPORT TESTS PASSED SUCCESSFULLY!")
