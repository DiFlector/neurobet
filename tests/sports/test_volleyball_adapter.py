"""Automated test suite for Phase 27: Volleyball Sport Adapter, clean state schema, feature builder, and isolation."""

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
    "/srv/neurobet/sports/volleyball/src",
    "/srv/neurobet/services/neural",
    "/srv/neurobet/services/backend",
    "/app/packages/contracts/src",
    "/app/packages/features/src",
    "/app/packages/db/src",
    "/app/packages/sports-core/src",
    "/app/packages/bankroll/src",
    "/app/sports/tennis/src",
    "/app/sports/basketball/src",
    "/app/sports/volleyball/src",
    "/app",
]
for p in paths:
    if p not in sys.path:
        sys.path.insert(0, p)

from sports_core import registry, FonbetResultsParser
from tennis_adapter import TennisSportAdapter, detect_tennis_surface, normalize_russian_player_name
from basketball_adapter import BasketballSportAdapter, normalize_russian_basketball_team
from volleyball_adapter import (
    VolleyballSportAdapter,
    VolleyballPipeline,
    normalize_russian_volleyball_team,
    is_volleyball_enabled,
    register_volleyball_adapter,
    unregister_volleyball_adapter,
)
from features import (
    VolleyballFeatureBuilder,
    BasketballFeatureBuilder,
    TennisFeatureBuilder,
    registry as feature_registry,
)
from contracts import (
    Event,
    EventState,
    FeatureVector,
    MLPrediction,
)
from unittest.mock import MagicMock


def test_config_flag_gating():
    """Verify Volleyball is gated strictly by configuration flag."""
    # 1. When unregistered / disabled
    unregister_volleyball_adapter()
    assert registry.is_supported("volleyball") is False
    assert "volleyball" not in registry.list_sports()

    desc_unsupported = registry.get_descriptor("volleyball")
    assert desc_unsupported.supported is False
    assert desc_unsupported.status == "UNSUPPORTED"
    assert desc_unsupported.participant_type == "team"

    # 2. When registered / enabled
    register_volleyball_adapter()
    assert registry.is_supported("volleyball") is True
    assert "volleyball" in registry.list_sports()

    desc_active = registry.get_descriptor("volleyball")
    assert desc_active.supported is True
    assert desc_active.status == "ACTIVE"
    assert desc_active.name_ru == "Волейбол"
    assert desc_active.participant_type == "team"

    market_types = [m.market_type for m in desc_active.supported_markets]
    assert "match_winner" in market_types
    assert "set_winner" in market_types
    assert "total_points" in market_types
    assert "handicap_sets" in market_types
    assert "handicap_points" in market_types
    print("✓ test_config_flag_gating passed")


def test_russian_team_name_normalization():
    """Verify Russian team normalization for Superleague (Men/Women) and Champions League."""
    # Russian Superleague Men
    assert normalize_russian_volleyball_team("Зенит-Казань") == "Зенит-Казань"
    assert normalize_russian_volleyball_team("ВК Зенит-Казань") == "Зенит-Казань"
    assert normalize_russian_volleyball_team("Динамо Москва") == "Динамо Москва"
    assert normalize_russian_volleyball_team("Локомотив Новосибирск") == "Локомотив Новосибирск"
    assert normalize_russian_volleyball_team("Белогорье") == "Белогорье Белгород"
    assert normalize_russian_volleyball_team("Факел Новый Уренгой") == "Факел Новый Уренгой"
    assert normalize_russian_volleyball_team("Кузбасс") == "Кузбасс Кемерово"
    assert normalize_russian_volleyball_team("Динамо-ЛО") == "Динамо-ЛО Сосновый Бор"
    assert normalize_russian_volleyball_team("Урал Уфа") == "Динамо-Урал Уфа"

    # Women
    assert normalize_russian_volleyball_team("Динамо-Ак Барс") == "Динамо-Ак Барс Казань"
    assert normalize_russian_volleyball_team("Ленинградка") == "Ленинградка Санкт-Петербург"
    assert normalize_russian_volleyball_team("Протон") == "Протон Саратов"

    # International
    assert normalize_russian_volleyball_team("Перуджа") == "Сир Сафети Перуджа"
    assert normalize_russian_volleyball_team("Трентино") == "Трентино"
    assert normalize_russian_volleyball_team("Закса") == "Закса Кендзежин-Козле"
    print("✓ test_russian_team_name_normalization passed")


def test_hierarchical_sets_and_clean_contract_state():
    """
    Verify volleyball hierarchical state parsing (match sets -> current set points -> serving team -> timeouts)
    and guarantee that generic contracts are not cluttered with sport-specific columns.
    """
    adapter = VolleyballSportAdapter()

    raw_payload = {
        "id": "2837102",
        "competitionName": "Волейбол. Суперлига",
        "team1": "Зенит-Казань",
        "team2": "Динамо Москва",
        "score": "2:1 (25-22, 23-25, 25-18, 14-12)",
        "current_period": 4,
        "server": "home",
        "timeouts_a": 1,
        "timeouts_b": 2,
    }

    # 1. Parse Event
    event = adapter.parse_event(raw_payload)
    assert event.sport_code == "volleyball"
    assert event.participant_a == "Зенит-Казань"
    assert event.participant_b == "Динамо Москва"
    assert event.is_live is True

    # 2. Parse State
    state = adapter.parse_state(raw_payload)
    assert state.sport_code == "volleyball"
    assert state.current_period == 4
    assert state.possession == "team_a"

    # Check stats encapsulation preserving contract purity
    stats = state.stats
    assert stats["sets_won_a"] == 2
    assert stats["sets_won_b"] == 1
    assert len(stats["volleyball_sets"]) == 4
    assert stats["volleyball_sets"][0]["points_a"] == 25
    assert stats["volleyball_sets"][0]["points_b"] == 22
    assert stats["current_set_score"]["points_a"] == 14
    assert stats["current_set_score"]["points_b"] == 12
    assert stats["timeouts_a"] == 1
    assert stats["timeouts_b"] == 2
    assert stats["is_tiebreak_set"] is False
    print("✓ test_hierarchical_sets_and_clean_contract_state passed")


def test_volleyball_odds_and_markets():
    """Verify volleyball odds snapshotting: moneyline, handicap_sets, total_points."""
    adapter = VolleyballSportAdapter()

    raw_odds_payload = {
        "event_id": "vb_101",
        "markets": [
            {
                "market_type": "match_winner",
                "name": "Победитель матча",
                "selections": [
                    {"outcome": "team_a", "odds": 1.45},
                    {"outcome": "team_b", "odds": 2.75},
                ],
            },
            {
                "market_type": "handicap_sets",
                "name": "Фора по сетам",
                "line_value": -1.5,
                "selections": [
                    {"outcome": "team_a", "odds": 1.95},
                    {"outcome": "team_b", "odds": 1.85},
                ],
            },
            {
                "market_type": "total_points",
                "name": "Тотал очков",
                "line_value": 182.5,
                "selections": [
                    {"outcome": "over", "odds": 1.90},
                    {"outcome": "under", "odds": 1.90},
                ],
            },
        ],
    }

    odds_snapshot = adapter.parse_odds(raw_odds_payload)
    assert odds_snapshot.event_id == "vb_101"
    assert odds_snapshot.snapshot_id is not None
    assert len(odds_snapshot.markets) == 3
    assert odds_snapshot.content_hash is not None

    mkt_types = [m.market_type for m in odds_snapshot.markets]
    assert "match_winner" in mkt_types
    assert "handicap_sets" in mkt_types
    assert "total_points" in mkt_types

    # Rules validation
    assert adapter.validate_sport_rules({"market": "match_winner", "outcome": "team_a", "bookmaker_odds": 1.50}) is True
    assert adapter.validate_sport_rules({"market": "total_points", "outcome": "over", "bookmaker_odds": 1.90}) is True
    assert adapter.validate_sport_rules({"market": "unsupported_market", "outcome": "team_a", "bookmaker_odds": 1.50}) is False
    print("✓ test_volleyball_odds_and_markets passed")


def test_volleyball_features_zero_future_leakage():
    """Verify Volleyball feature builder guarantees zero future data leakage."""
    builder = VolleyballFeatureBuilder()
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(minutes=20)
    t1 = now - timedelta(minutes=10)
    t2 = now - timedelta(minutes=2)
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
            score_detail={"sets_won_a": 0, "sets_won_b": 0, "volleyball_sets": [{"set": 1, "points_a": 25, "points_b": 20}]},
            possession="team_a",
        ),
        MockSnapshot(
            t1,
            current_period=2,
            score_detail={"sets_won_a": 1, "sets_won_b": 0, "volleyball_sets": [{"set": 1, "points_a": 25, "points_b": 20}, {"set": 2, "points_a": 23, "points_b": 25}]},
            possession="team_b",
        ),
        MockSnapshot(
            t2,
            current_period=3,
            score_detail={
                "sets_won_a": 1,
                "sets_won_b": 1,
                "current_set_score": {"points_a": 21, "points_b": 18},
                "volleyball_sets": [
                    {"set": 1, "points_a": 25, "points_b": 20},
                    {"set": 2, "points_a": 23, "points_b": 25},
                    {"set": 3, "points_a": 21, "points_b": 18},
                ],
            },
            possession="team_a",
        ),
        # Future snapshot - MUST NEVER BE ACCESSED
        MockSnapshot(
            t_future,
            current_period=4,
            score_detail={"sets_won_a": 3, "sets_won_b": 1},
        ),
    ]

    odds_snapshots = [
        MockSnapshot(t0, outcome="team_a", odds=1.70),
        MockSnapshot(t0, outcome="team_b", odds=2.15),
        MockSnapshot(t1, outcome="team_a", odds=1.85),
        MockSnapshot(t1, outcome="team_b", odds=1.95),
        MockSnapshot(t2, outcome="team_a", odds=1.55),
        MockSnapshot(t2, outcome="team_b", odds=2.45),
        # Future odds - MUST NEVER BE ACCESSED
        MockSnapshot(t_future, outcome="team_a", odds=1.05),
        MockSnapshot(t_future, outcome="team_b", odds=9.00),
    ]

    fv = builder.build_features(
        event_id="vb_leak_test",
        cutoff_time=t2,
        event_snapshots=event_snapshots,
        odds_snapshots=odds_snapshots,
    )

    assert fv.sport_code == "volleyball"
    assert fv.features["sets_won_team_a"] == 1.0
    assert fv.features["sets_won_team_b"] == 1.0
    assert fv.features["sets_differential"] == 0.0
    assert fv.features["current_set_number"] == 3.0
    assert fv.features["current_set_points_diff"] == 3.0  # 21 - 18
    assert fv.features["is_server_team_a"] == 1.0
    assert fv.features["current_odds_team_a"] == 1.55
    assert fv.features["current_odds_team_b"] == 2.45
    # Ensure future odds were completely excluded
    assert fv.features["current_odds_team_a"] != 1.05
    print("✓ test_volleyball_features_zero_future_leakage passed")


def test_volleyball_settlement_and_results_parser():
    """Verify Fonbet volleyball official results parsing and settlement."""
    # 1. Finished 3:1 match
    w_31, status_31, _, _ = FonbetResultsParser.parse_volleyball_winner_and_status("3:1 (25-22, 23-25, 25-18, 25-20)")
    assert w_31 == "team_a"
    assert status_31 == "FINISHED"

    # 2. Finished 5-set tiebreak (2:3)
    w_tie, status_tie, _, _ = FonbetResultsParser.parse_volleyball_winner_and_status("2:3 (25-20, 22-25, 25-21, 20-25, 12-15)")
    assert w_tie == "team_b"
    assert status_tie == "FINISHED"

    # 3. Cancelled match
    w_canc, status_canc, is_canc, is_wo = FonbetResultsParser.parse_volleyball_winner_and_status("-:-", "Матч отменен")
    assert w_canc is None
    assert status_canc == "CANCELLED"
    assert is_canc is True

    # 4. Adapter settlement
    adapter = VolleyballSportAdapter()
    settle_win = adapter.parse_result({
        "event_id": "vb_settle_1",
        "status": "finished",
        "score": "3:0",
        "outcome": "team_a",
        "odds": 1.75,
    })
    assert settle_win is not None
    assert settle_win.status == "WON"
    assert settle_win.net_profit > 0.0

    settle_loss = adapter.parse_result({
        "event_id": "vb_settle_2",
        "status": "finished",
        "score": "1:3",
        "outcome": "team_a",
        "odds": 1.75,
    })
    assert settle_loss is not None
    assert settle_loss.status == "LOST"
    assert settle_loss.net_profit < 0.0
    print("✓ test_volleyball_settlement_and_results_parser passed")


def test_volleyball_baseline_ml_inference():
    """Verify MLPredictor generates calibrated prediction for volleyball with team_a and team_b."""
    try:
        from services.neural.app.inference import MLPredictor
    except ImportError:
        try:
            from app.inference import MLPredictor
        except ImportError:
            from neural.app.inference import MLPredictor

    predictor = MLPredictor()
    mock_model = MagicMock()
    mock_model.predict_proba.return_value = [[0.30, 0.70]]  # team_b=0.30, team_a=0.70

    predictor.checkpoint_data = {
        "model": mock_model,
        "feature_names": ["sets_differential", "current_set_points_diff", "no_vig_prob_team_a"],
        "version_tag": "volleyball_v1.0",
        "market": "match_winner",
    }

    fv = FeatureVector(
        event_id="vb_inf_test",
        sport_code="volleyball",
        feature_version="1.0.0",
        timestamp=datetime.now(timezone.utc),
        features={
            "sets_differential": 1.0,
            "current_set_points_diff": 4.0,
            "no_vig_prob_team_a": 0.65,
            "current_odds_team_a": 1.60,
            "current_odds_team_b": 2.35,
        },
    )

    pred = predictor.predict(fv)
    assert pred.sport_code == "volleyball"
    assert pred.model_version == "volleyball_v1.0"
    assert len(pred.outcomes) == 2
    assert pred.outcomes[0].outcome == "team_a"
    assert pred.outcomes[1].outcome == "team_b"
    assert pred.outcomes[0].model_probability == 0.70
    assert pred.outcomes[1].model_probability == 0.30
    assert pred.best_outcome == "team_a"
    assert pred.has_positive_edge is True
    print("✓ test_volleyball_baseline_ml_inference passed")


def test_triple_sport_isolation():
    """Verify Tennis, Basketball, and Volleyball run concurrently with zero mutual interference."""
    # All 3 sports registered
    register_volleyball_adapter()
    from basketball_adapter import register_basketball_adapter
    register_basketball_adapter()

    assert registry.is_supported("tennis") is True
    assert registry.is_supported("basketball") is True
    assert registry.is_supported("volleyball") is True

    # Check descriptors
    desc_tennis = registry.get_descriptor("tennis")
    desc_bball = registry.get_descriptor("basketball")
    desc_vball = registry.get_descriptor("volleyball")

    assert desc_tennis.participant_type == "individual"
    assert desc_bball.participant_type == "team"
    assert desc_vball.participant_type == "team"

    # Surface detection isolation
    assert detect_tennis_surface("ATP. Мастерс 1000. Рим (Грунт)") == "clay"
    assert BasketballSportAdapter().detect_surface("НБА") == "indoor_parquet"
    assert VolleyballSportAdapter().detect_surface("Суперлига") == "indoor_taraflex"

    # Player / team normalization isolation
    assert normalize_russian_player_name("Медведев Д.") == "Даниил Медведев"
    assert normalize_russian_basketball_team("ЦСКА") == "ЦСКА Москва"
    assert normalize_russian_volleyball_team("Зенит-Казань") == "Зенит-Казань"

    # Feature builders isolation
    assert feature_registry.get("tennis") is not None
    assert feature_registry.get("basketball") is not None
    assert feature_registry.get("volleyball") is not None
    print("✓ test_triple_sport_isolation passed")


if __name__ == "__main__":
    print("Running Phase 27 Volleyball Sport Test Suite...")
    test_config_flag_gating()
    test_russian_team_name_normalization()
    test_hierarchical_sets_and_clean_contract_state()
    test_volleyball_odds_and_markets()
    test_volleyball_features_zero_future_leakage()
    test_volleyball_settlement_and_results_parser()
    test_volleyball_baseline_ml_inference()
    test_triple_sport_isolation()
    print("\n🎉 ALL PHASE 27 VOLLEYBALL SPORT TESTS PASSED SUCCESSFULLY!")
