"""Automated test suite for Phase 28: Hockey Sport Adapter, clean state schema, feature builder, and quadruple isolation."""

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
    "/srv/neurobet/sports/hockey/src",
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
    "/app/sports/hockey/src",
    "/app",
]
for p in paths:
    if p not in sys.path:
        sys.path.insert(0, p)

from sports_core import registry, FonbetResultsParser
from tennis_adapter import TennisSportAdapter
from basketball_adapter import BasketballSportAdapter
from volleyball_adapter import VolleyballSportAdapter
from hockey_adapter import (
    HockeySportAdapter,
    HockeyPipeline,
    normalize_russian_hockey_team,
    is_hockey_enabled,
    register_hockey_adapter,
    unregister_hockey_adapter,
)
from features import (
    HockeyFeatureBuilder,
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
    """Verify that Hockey adapter is enabled strictly via config flag and unregisters cleanly."""
    # Ensure disabled by default when env flag is unset
    os.environ.pop("ENABLE_HOCKEY", None)
    os.environ.pop("SPORT_HOCKEY_ENABLED", None)
    assert not is_hockey_enabled(), "Hockey should be disabled by default"

    unregister_hockey_adapter()
    assert registry.get("hockey") is None, "Hockey should not be in registry when disabled"

    # Enable via config flag
    os.environ["ENABLE_HOCKEY"] = "true"
    assert is_hockey_enabled(), "Hockey should be enabled when ENABLE_HOCKEY=true"

    register_hockey_adapter()
    adapter = registry.get("hockey")
    assert adapter is not None, "Hockey adapter should be registered in registry"
    assert adapter.sport_code == "hockey"

    # Cleanup
    unregister_hockey_adapter()
    assert registry.get("hockey") is None, "Hockey adapter should unregister cleanly"
    os.environ.pop("ENABLE_HOCKEY", None)
    print("✓ test_config_flag_gating passed")


def test_russian_team_name_normalization():
    """Verify normalization of Russian KHL and NHL hockey clubs."""
    # KHL teams
    assert normalize_russian_hockey_team("хк цска") == "ЦСКА Москва"
    assert normalize_russian_hockey_team("ЦСКА (Москва)") == "ЦСКА Москва"
    assert normalize_russian_hockey_team("СКА спб") == "СКА Санкт-Петербург"
    assert normalize_russian_hockey_team("Ак Барс (Казань)") == "Ак Барс Казань"
    assert normalize_russian_hockey_team("металлург магнитогорск") == "Металлург Магнитогорск"
    assert normalize_russian_hockey_team("авангард омск") == "Авангард Омск"
    assert normalize_russian_hockey_team("локомотив ярославль") == "Локомотив Ярославль"
    assert normalize_russian_hockey_team("хк сочи") == "ХК Сочи"
    assert normalize_russian_hockey_team("куньлунь ред стар") == "Куньлунь Ред Стар"
    assert normalize_russian_hockey_team("трактор") == "Трактор Челябинск"

    # NHL teams
    assert normalize_russian_hockey_team("вашингтон") == "Вашингтон Кэпиталз"
    assert normalize_russian_hockey_team("тампа-бэй") == "Тампа-Бэй Лайтнинг"
    assert normalize_russian_hockey_team("рейнджерс") == "Нью-Йорк Рейнджерс"
    assert normalize_russian_hockey_team("флорида пантерз") == "Флорида Пантерз"
    assert normalize_russian_hockey_team("эдмонтон ойлерз") == "Эдмонтон Ойлерз"

    # Unknown club fallback
    assert normalize_russian_hockey_team("ХК Неизвестный Клуб") == "ХК Неизвестный Клуб"
    print("✓ test_russian_team_name_normalization passed")


def test_hierarchical_periods_and_clean_contract_state():
    """Verify period parsing, overtime, shots on goal, powerplays, and clean contract purity."""
    adapter = HockeySportAdapter()

    # Raw live payload from Fonbet
    raw_event = {
        "id": "11223344",
        "competitionName": "Хоккей. КХЛ. Регулярный чемпионат",
        "team1": "хк цска",
        "team2": "ска спб",
        "scheduled_start": "2026-10-15T16:30:00Z",
        "is_live": True,
        "score": "3:2 (1:0, 1:1, 1:1)",
        "current_period": 3,
        "current_time": "14:22",
        "shots_on_goal": {"team_a": 34, "team_b": 28},
        "powerplay": {"is_active": True, "team": "team_a", "man_advantage": "5on4"},
        "empty_net_a": False,
        "empty_net_b": True,
    }

    event = adapter.parse_event(raw_event)
    assert event.sport_code == "hockey"
    assert event.participant_a == "ЦСКА Москва"
    assert event.participant_b == "СКА Санкт-Петербург"
    assert event.tournament == "Хоккей. КХЛ. Регулярный чемпионат"

    state = adapter.parse_state(raw_event)
    assert isinstance(state, EventState)
    assert state.sport_code == "hockey"
    assert state.current_period == 3
    assert state.possession == "team_a"  # On powerplay

    # Sport-specific data encapsulated in stats
    stats = state.stats
    assert stats["score_a"] == 3
    assert stats["score_b"] == 2
    assert len(stats["hockey_periods"]) == 3
    assert stats["hockey_periods"][0] == {"period": 1, "goals_a": 1, "goals_b": 0}
    assert stats["hockey_periods"][1] == {"period": 2, "goals_a": 1, "goals_b": 1}
    assert stats["hockey_periods"][2] == {"period": 3, "goals_a": 1, "goals_b": 1}
    assert stats["shots_on_goal"] == {"team_a": 34, "team_b": 28}
    assert stats["powerplay"]["is_active"] is True
    assert stats["powerplay"]["team"] == "team_a"
    assert stats["empty_net_b"] is True
    assert stats["is_overtime"] is False

    # Contract purity: generic fields are clean
    assert state.sets == []
    print("✓ test_hierarchical_periods_and_clean_contract_state passed")


def test_hockey_odds_and_markets():
    """Verify parsing of hockey markets and deduplication via content_hash."""
    adapter = HockeySportAdapter()

    raw_payload = {
        "id": "998877",
        "markets": [
            {
                "market_type": "match_winner",
                "selections": [
                    {"name": "team_a", "odds": 1.75},
                    {"name": "team_b", "odds": 2.10},
                ],
            },
            {
                "market_type": "match_winner_regular",
                "selections": [
                    {"name": "team_a", "odds": 2.15},
                    {"name": "draw", "odds": 3.90},
                    {"name": "team_b", "odds": 2.80},
                ],
            },
            {
                "market_type": "total_goals",
                "param": 5.5,
                "selections": [
                    {"name": "over", "odds": 1.95},
                    {"name": "under", "odds": 1.85},
                ],
            },
            {
                "market_type": "handicap_goals",
                "param": -1.5,
                "selections": [
                    {"name": "team_a", "odds": 2.30},
                    {"name": "team_b", "odds": 1.62},
                ],
            },
        ],
    }

    odds_snapshot = adapter.parse_odds_snapshot(raw_payload)
    assert len(odds_snapshot.markets) == 4
    m_types = [m.market_type for m in odds_snapshot.markets]
    assert "match_winner" in m_types
    assert "match_winner_regular" in m_types
    assert "total_goals" in m_types
    assert "handicap_goals" in m_types

    # Deduplication hash
    assert odds_snapshot.content_hash is not None
    odds_snapshot_2 = adapter.parse_odds_snapshot(raw_payload)
    assert odds_snapshot.content_hash == odds_snapshot_2.content_hash
    print("✓ test_hockey_odds_and_markets passed")


def test_hockey_features_zero_future_leakage():
    """Verify HockeyFeatureBuilder enforces point-in-time cutoff with zero future data leakage."""
    builder = HockeyFeatureBuilder()

    t0 = datetime(2026, 10, 15, 17, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 15, 17, 30, 0, tzinfo=timezone.utc)  # Cutoff
    t2_future = datetime(2026, 10, 15, 18, 0, 0, tzinfo=timezone.utc)

    # State snapshots
    s0 = MagicMock()
    s0.observed_at = t0
    s0.current_period = 1
    s0.score_a = 1
    s0.score_b = 0
    s0.score_detail = {"score_a": 1, "score_b": 0, "current_period": 1, "shots_on_goal": {"team_a": 10, "team_b": 8}}

    s1 = MagicMock()
    s1.observed_at = t1
    s1.current_period = 2
    s1.score_a = 2
    s1.score_b = 1
    s1.score_detail = {
        "score_a": 2,
        "score_b": 1,
        "current_period": 2,
        "shots_on_goal": {"team_a": 22, "team_b": 18},
        "powerplay": {"is_active": True, "team": "team_a"},
    }

    s2_future = MagicMock()
    s2_future.observed_at = t2_future
    s2_future.current_period = 3
    s2_future.score_a = 5
    s2_future.score_b = 1
    s2_future.score_detail = {"score_a": 5, "score_b": 1, "current_period": 3}

    # Odds snapshots
    o0_a = MagicMock(observed_at=t0, outcome="team_a", odds=1.85)
    o0_b = MagicMock(observed_at=t0, outcome="team_b", odds=1.95)

    o1_a = MagicMock(observed_at=t1, outcome="team_a", odds=1.45)
    o1_b = MagicMock(observed_at=t1, outcome="team_b", odds=2.75)

    o2_future_a = MagicMock(observed_at=t2_future, outcome="team_a", odds=1.02)
    o2_future_b = MagicMock(observed_at=t2_future, outcome="team_b", odds=18.0)

    # Build features with cutoff strictly at t1
    features_vec = builder.build_features(
        event_id="hockey_match_1",
        cutoff_time=t1,
        event_snapshots=[s0, s1, s2_future],
        odds_snapshots=[o0_a, o0_b, o1_a, o1_b, o2_future_a, o2_future_b],
    )

    f = features_vec.features
    # Must reflect t1 state (score 2:1), NOT future t2 (score 5:1)
    assert f["score_team_a"] == 2.0
    assert f["score_team_b"] == 1.0
    assert f["goal_differential"] == 1.0
    assert f["total_goals"] == 3.0
    assert f["current_period"] == 2.0
    assert f["shots_team_a"] == 22.0
    assert f["shots_team_b"] == 18.0
    assert f["shots_differential"] == 4.0
    assert f["is_powerplay"] == 1.0
    assert f["is_powerplay_team_a"] == 1.0

    # Must reflect t1 odds (1.45), NOT future t2 odds (1.02)
    assert f["current_odds_team_a"] == 1.45
    assert f["current_odds_team_b"] == 2.75
    assert f["no_vig_prob_team_a"] > f["no_vig_prob_team_b"]
    print("✓ test_hockey_features_zero_future_leakage passed")


def test_hockey_settlement_and_results_parser():
    """Verify Fonbet results parser for regular time, overtime, shootout, and bet settlement."""
    # 1. Official Fonbet Results feed parser
    w, st, canc, wo = FonbetResultsParser.parse_hockey_winner_and_status("3:2 (1:0, 1:1, 1:1)")
    assert w == "team_a"
    assert st == "FINISHED"

    w, st, canc, wo = FonbetResultsParser.parse_hockey_winner_and_status("4:3 ОТ (1:1, 1:1, 1:1, 1:0)")
    assert w == "team_a"
    assert st == "FINISHED"

    w, st, canc, wo = FonbetResultsParser.parse_hockey_winner_and_status("2:3 Б (0:0, 1:1, 1:1, 0:0, 0:1)")
    assert w == "team_b"
    assert st == "FINISHED"

    w, st, canc, wo = FonbetResultsParser.parse_hockey_winner_and_status("-:-", "Матч отменен")
    assert w is None
    assert st == "CANCELLED"

    # 2. Adapter settlement logic
    adapter = HockeySportAdapter()

    # match_winner: team_a won in OT
    final_ot = {"score_a": 4, "score_b": 3, "reg_score_a": 3, "reg_score_b": 3}
    settle_mw = adapter.parse_settlement(
        {"bet_id": "b1", "market_type": "match_winner", "selection": "team_a", "amount": 1000, "odds": 1.80},
        final_ot,
    )
    assert settle_mw.status == "WON"
    assert settle_mw.payout == 1800.0

    # match_winner_regular: bet on draw in 60 mins
    settle_mwr = adapter.parse_settlement(
        {"bet_id": "b2", "market_type": "match_winner_regular", "selection": "draw", "amount": 1000, "odds": 4.0},
        final_ot,
    )
    assert settle_mwr.status == "WON"
    assert settle_mwr.payout == 4000.0

    # total_goals: over 5.5 (4+3=7)
    settle_tot = adapter.parse_settlement(
        {"bet_id": "b3", "market_type": "total_goals", "selection": "over", "amount": 1000, "odds": 1.95, "param": 5.5},
        final_ot,
    )
    assert settle_tot.status == "WON"

    # handicap_goals: team_a -1.5 with final 4:3 (diff 4 - 1.5 - 3 = -0.5 -> LOST)
    settle_hcap = adapter.parse_settlement(
        {"bet_id": "b4", "market_type": "handicap_goals", "selection": "team_a", "amount": 1000, "odds": 2.20, "param": -1.5},
        final_ot,
    )
    assert settle_hcap.status == "LOST"
    print("✓ test_hockey_settlement_and_results_parser passed")


def test_hockey_baseline_ml_inference():
    """Verify baseline ML inference generates structured predictions with team_a/team_b outcomes."""
    try:
        from services.neural.app.inference import MLPredictor
    except ImportError:
        try:
            from app.inference import MLPredictor
        except ImportError:
            from neural.app.inference import MLPredictor

    predictor = MLPredictor()
    mock_model = MagicMock()
    mock_model.predict_proba.return_value = [[0.42, 0.58]]  # team_b=0.42, team_a=0.58

    predictor.checkpoint_data = {
        "model": mock_model,
        "feature_names": ["goal_differential", "shots_differential", "no_vig_prob_team_a"],
        "version_tag": "hockey_v1.0",
        "market": "match_winner",
    }

    f_vec = FeatureVector(
        event_id="hockey_ml_test_1",
        sport_code="hockey",
        feature_version="1.0.0",
        timestamp=datetime.now(timezone.utc),
        features={
            "current_odds_team_a": 1.65,
            "current_odds_team_b": 2.25,
            "no_vig_prob_team_a": 0.58,
            "no_vig_prob_team_b": 0.42,
            "goal_differential": 1.0,
            "shots_differential": 8.0,
            "is_powerplay": 1.0,
        },
    )

    pred = predictor.predict(f_vec)
    assert isinstance(pred, MLPrediction)
    assert pred.sport_code == "hockey"
    assert pred.model_version == "hockey_v1.0"

    outcomes = {o.outcome: o for o in pred.outcomes}
    assert "team_a" in outcomes
    assert "team_b" in outcomes
    assert abs((outcomes["team_a"].model_probability + outcomes["team_b"].model_probability) - 1.0) < 0.001
    assert outcomes["team_a"].fair_odds > 1.0
    assert outcomes["team_b"].fair_odds > 1.0
    print("✓ test_hockey_baseline_ml_inference passed")


def test_quadruple_sport_isolation():
    """Verify that Tennis, Basketball, Volleyball, and Hockey adapters coexist without interference."""
    tennis_adapter = TennisSportAdapter()
    basketball_adapter = BasketballSportAdapter()
    volleyball_adapter = VolleyballSportAdapter()
    hockey_adapter = HockeySportAdapter()

    # Register all four sports
    registry.register(tennis_adapter)
    registry.register(basketball_adapter)
    registry.register(volleyball_adapter)
    registry.register(hockey_adapter)

    assert registry.get("tennis") is tennis_adapter
    assert registry.get("basketball") is basketball_adapter
    assert registry.get("volleyball") is volleyball_adapter
    assert registry.get("hockey") is hockey_adapter

    # Verify sport codes
    assert registry.get("tennis").sport_code == "tennis"
    assert registry.get("basketball").sport_code == "basketball"
    assert registry.get("volleyball").sport_code == "volleyball"
    assert registry.get("hockey").sport_code == "hockey"

    # Verify feature builders in registry
    assert feature_registry.get("tennis") is not None
    assert feature_registry.get("basketball") is not None
    assert feature_registry.get("volleyball") is not None
    assert feature_registry.get("hockey") is not None

    print("✓ test_quadruple_sport_isolation passed")


if __name__ == "__main__":
    print("Running Phase 28 Hockey Sport Test Suite...")
    test_config_flag_gating()
    test_russian_team_name_normalization()
    test_hierarchical_periods_and_clean_contract_state()
    test_hockey_odds_and_markets()
    test_hockey_features_zero_future_leakage()
    test_hockey_settlement_and_results_parser()
    test_hockey_baseline_ml_inference()
    test_quadruple_sport_isolation()
    print("\n🎉 ALL PHASE 28 HOCKEY SPORT TESTS PASSED SUCCESSFULLY!")
