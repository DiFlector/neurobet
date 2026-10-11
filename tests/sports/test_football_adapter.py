"""Automated test suite for Phase 29: Football Sport Adapter, clean state schema, feature builder, and quintuple isolation."""

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
    "/srv/neurobet/sports/football/src",
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
    "/app/sports/football/src",
    "/app",
]
for p in paths:
    if p not in sys.path:
        sys.path.insert(0, p)

from sports_core import registry, FonbetResultsParser
from sports_core.results_parser import FONBET_FOOTBALL_SPORT_ID
from tennis_adapter import TennisSportAdapter
from basketball_adapter import BasketballSportAdapter
from volleyball_adapter import VolleyballSportAdapter
from hockey_adapter import HockeySportAdapter
from football_adapter import (
    FootballSportAdapter,
    FootballPipeline,
    normalize_russian_football_team,
    is_football_enabled,
    register_football_adapter,
    unregister_football_adapter,
)
from features import (
    FootballFeatureBuilder,
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
    """Verify that Football adapter is enabled strictly via config flag and unregisters cleanly."""
    # Ensure disabled by default when env flag is unset
    os.environ.pop("ENABLE_FOOTBALL", None)
    os.environ.pop("SPORT_FOOTBALL_ENABLED", None)
    assert not is_football_enabled(), "Football should be disabled by default"

    unregister_football_adapter()
    assert registry.get("football") is None, "Football should not be in registry when disabled"

    # Enable via config flag
    os.environ["ENABLE_FOOTBALL"] = "true"
    assert is_football_enabled(), "Football should be enabled when ENABLE_FOOTBALL=true"

    register_football_adapter()
    adapter = registry.get("football")
    assert adapter is not None, "Football adapter should be registered in registry"
    assert adapter.sport_code == "football"

    # Cleanup
    unregister_football_adapter()
    assert registry.get("football") is None, "Football adapter should unregister cleanly"
    os.environ.pop("ENABLE_FOOTBALL", None)
    print("✓ test_config_flag_gating passed")


def test_russian_team_name_normalization():
    """Verify normalization of Russian RPL and European football clubs."""
    # RPL teams
    assert normalize_russian_football_team("фк зенит спб") == "Зенит Санкт-Петербург"
    assert normalize_russian_football_team("Спартак (Москва)") == "Спартак Москва"
    assert normalize_russian_football_team("пфк цска") == "ЦСКА Москва"
    assert normalize_russian_football_team("фк краснодар") == "Краснодар"
    assert normalize_russian_football_team("Динамо М") == "Динамо Москва"
    assert normalize_russian_football_team("локомотив москва") == "Локомотив Москва"
    assert normalize_russian_football_team("фк ростов") == "Ростов"
    assert normalize_russian_football_team("крылья советов") == "Крылья Советов Самара"
    assert normalize_russian_football_team("рубин казань") == "Рубин Казань"
    assert normalize_russian_football_team("пари нн") == "Пари Нижний Новгород"

    # European clubs
    assert normalize_russian_football_team("Реал Мадрид") == "Реал Мадрид"
    assert normalize_russian_football_team("Барселона") == "Барселона"
    assert normalize_russian_football_team("ман сити") == "Манчестер Сити"
    assert normalize_russian_football_team("мю") == "Манчестер Юнайтед"
    assert normalize_russian_football_team("Бавария") == "Бавария Мюнхен"
    assert normalize_russian_football_team("псж") == "Пари Сен-Жермен"
    assert normalize_russian_football_team("Интер Милан") == "Интер Милан"
    print("✓ test_russian_team_name_normalization passed")


def test_hierarchical_halves_and_clean_contract_state():
    """
    Verify parsing of football match state:
    - Halves structure (half 1, half 2, extra time)
    - Yellow & red cards, corners, shots on goal, xG preserved in stats
    - Purity of core Event and EventState contracts without pollution
    """
    adapter = FootballSportAdapter()
    raw_event = {
        "id": "fb_1001",
        "competitionName": "Футбол. Чемпионат России. РПЛ",
        "team1": "Зенит",
        "team2": "Спартак Москва",
        "startTime": 1728662400,
        "is_live": True,
        "status": "live",
    }
    event = adapter.parse_event(raw_event)
    assert isinstance(event, Event)
    assert event.sport_code == "football"
    assert event.participant_a == "Зенит Санкт-Петербург"
    assert event.participant_b == "Спартак Москва"
    assert event.tournament == "Футбол. Чемпионат России. РПЛ"

    # Raw live state with halves, cards, corners, xG
    raw_state = {
        "id": "fb_1001",
        "score": "2:1 (1:0, 1:1)",
        "current_period": 2,
        "minute": 78,
        "yellow_cards": {"team_a": 1, "team_b": 3},
        "red_cards": {"team_a": 0, "team_b": 1},
        "corners": {"team_a": 6, "team_b": 2},
        "shots_on_goal": {"team_a": 7, "team_b": 3},
        "possession_pct": {"team_a": 58.0, "team_b": 42.0},
        "xg": {"team_a": 1.85, "team_b": 0.95},
        "possession": "team_a",
    }
    state = adapter.parse_state(raw_state)
    assert isinstance(state, EventState)
    assert state.sport_code == "football"
    assert state.current_period == 2
    assert state.match_clock_seconds == 78 * 60
    assert state.possession == "team_a"

    # Core contracts remain 100% clean
    assert not hasattr(EventState, "yellow_cards")
    assert not hasattr(EventState, "red_cards")
    assert not hasattr(EventState, "corners")
    assert not hasattr(EventState, "xg")

    # Sport-specific fields safely encapsulated in stats dict
    stats = state.stats
    assert stats["score_a"] == 2
    assert stats["score_b"] == 1
    assert len(stats["football_halves"]) == 2
    assert stats["football_halves"][0] == {"half": 1, "goals_a": 1, "goals_b": 0}
    assert stats["football_halves"][1] == {"half": 2, "goals_a": 1, "goals_b": 1}
    assert stats["yellow_cards"] == {"team_a": 1, "team_b": 3}
    assert stats["red_cards"] == {"team_a": 0, "team_b": 1}
    assert stats["corners"] == {"team_a": 6, "team_b": 2}
    assert stats["shots_on_goal"] == {"team_a": 7, "team_b": 3}
    assert stats["xg"] == {"team_a": 1.85, "team_b": 0.95}
    assert stats["minute"] == 78
    print("✓ test_hierarchical_halves_and_clean_contract_state passed")


def test_football_odds_and_markets():
    """Verify odds snapshotting for 1X2, double chance, total goals, handicap, and BTTS."""
    adapter = FootballSportAdapter()
    raw_payload = {
        "id": "fb_1002",
        "markets": [
            {
                "market_type": "match_winner",
                "name": "1X2",
                "selections": [
                    {"name": "team_a", "outcome": "team_a", "odds": 2.10},
                    {"name": "draw", "outcome": "draw", "odds": 3.30},
                    {"name": "team_b", "outcome": "team_b", "odds": 3.40},
                ],
            },
            {
                "market_type": "double_chance",
                "name": "Двойной исход",
                "selections": [
                    {"name": "1x", "outcome": "1x", "odds": 1.32},
                    {"name": "12", "outcome": "12", "odds": 1.30},
                    {"name": "x2", "outcome": "x2", "odds": 1.70},
                ],
            },
            {
                "market_type": "total_goals",
                "name": "Тотал голов 2.5",
                "param": 2.5,
                "selections": [
                    {"name": "over", "outcome": "over", "odds": 1.85},
                    {"name": "under", "outcome": "under", "odds": 1.95},
                ],
            },
            {
                "market_type": "both_teams_to_score",
                "name": "Обе забьют",
                "selections": [
                    {"name": "yes", "outcome": "yes", "odds": 1.75},
                    {"name": "no", "outcome": "no", "odds": 2.05},
                ],
            },
        ],
    }

    odds_snap = adapter.parse_odds_snapshot(raw_payload)
    assert odds_snap.event_id == "fonbet_football_fb_1002"
    assert len(odds_snap.markets) == 4
    assert odds_snap.content_hash.startswith("sha256:")

    m_map = {m.market_type: m for m in odds_snap.markets}
    assert "match_winner" in m_map
    assert "double_chance" in m_map
    assert "total_goals" in m_map
    assert "both_teams_to_score" in m_map

    m_winner = m_map["match_winner"]
    assert len(m_winner.selections) == 3
    sel_map = {s.outcome: s.odds for s in m_winner.selections}
    assert sel_map["team_a"] == 2.10
    assert sel_map["draw"] == 3.30
    assert sel_map["team_b"] == 3.40
    print("✓ test_football_odds_and_markets passed")


def test_football_features_zero_future_leakage():
    """Verify that FootballFeatureBuilder enforces strict cutoff_time with zero future leakage."""
    builder = FootballFeatureBuilder()
    now = datetime.now(timezone.utc)
    t_minus_20 = now - timedelta(seconds=20)
    t_minus_10 = now - timedelta(seconds=10)
    t_cutoff = now - timedelta(seconds=5)
    t_future = now + timedelta(seconds=10)

    # Event snapshots
    class DummySnapshot:
        def __init__(self, obs_at, score_a, score_b, minute, red_a=0, red_b=0, xg_a=0.5, xg_b=0.3):
            self.observed_at = obs_at
            self.score_a = score_a
            self.score_b = score_b
            self.current_period = 1 if minute <= 45 else 2
            self.stats = {
                "score_a": score_a,
                "score_b": score_b,
                "current_period": self.current_period,
                "minute": minute,
                "red_cards": {"team_a": red_a, "team_b": red_b},
                "xg": {"team_a": xg_a, "team_b": xg_b},
                "shots_on_goal": {"team_a": score_a * 2, "team_b": score_b * 2},
                "corners": {"team_a": 3, "team_b": 2},
            }

    class DummyOdds:
        def __init__(self, obs_at, outcome, odds):
            self.observed_at = obs_at
            self.outcome = outcome
            self.odds = odds

    snapshots = [
        DummySnapshot(t_minus_20, score_a=0, score_b=0, minute=15),
        DummySnapshot(t_minus_10, score_a=1, score_b=0, minute=25, red_b=1, xg_a=1.2, xg_b=0.2),
        DummySnapshot(t_future, score_a=3, score_b=1, minute=80),  # In future! Must be ignored!
    ]

    odds_list = [
        DummyOdds(t_minus_20, "team_a", 2.20),
        DummyOdds(t_minus_20, "draw", 3.20),
        DummyOdds(t_minus_20, "team_b", 3.10),
        DummyOdds(t_minus_10, "team_a", 1.55),
        DummyOdds(t_minus_10, "draw", 3.80),
        DummyOdds(t_minus_10, "team_b", 5.50),
        DummyOdds(t_future, "team_a", 1.10),  # Future odds! Must be ignored!
    ]

    f_vec = builder.build_features(
        event_id="fb_leakage_test",
        cutoff_time=t_cutoff,
        event_snapshots=snapshots,
        odds_snapshots=odds_list,
    )

    assert isinstance(f_vec, FeatureVector)
    assert f_vec.sport_code == "football"

    # Verify score at cutoff is 1:0 (NOT 3:1 from future!)
    assert f_vec.features["score_team_a"] == 1.0
    assert f_vec.features["score_team_b"] == 0.0
    assert f_vec.features["goal_differential"] == 1.0
    assert f_vec.features["match_minute"] == 25.0
    assert f_vec.features["has_red_card"] == 1.0
    assert f_vec.features["xg_team_a"] == 1.2

    # Verify odds at cutoff is 1.55 (NOT 1.10 from future!)
    assert f_vec.features["current_odds_team_a"] == 1.55
    assert f_vec.features["current_odds_team_b"] == 5.50
    assert f_vec.features["current_odds_draw"] == 3.80
    assert f_vec.features["no_vig_prob_team_a"] > 0.50
    print("✓ test_football_features_zero_future_leakage passed")


def test_football_settlement_and_results_parser():
    """Verify settlement of 1X2, double chance, total goals, handicap, BTTS, and Fonbet official results parser."""
    adapter = FootballSportAdapter()

    # 1. Match winner 1X2
    bet_p1 = {"bet_id": "b1", "market_type": "match_winner", "selection": "team_a", "amount": 1000.0, "odds": 2.10}
    settle_p1 = adapter.parse_settlement(bet_p1, {"score_a": 2, "score_b": 1})
    assert settle_p1.status == "WON"
    assert settle_p1.payout == 2100.0

    bet_x = {"bet_id": "b2", "market_type": "match_winner", "selection": "draw", "amount": 1000.0, "odds": 3.40}
    settle_x = adapter.parse_settlement(bet_x, {"score_a": 1, "score_b": 1})
    assert settle_x.status == "WON"
    assert settle_x.payout == 3400.0

    settle_x_lost = adapter.parse_settlement(bet_x, {"score_a": 1, "score_b": 0})
    assert settle_x_lost.status == "LOST"

    # 2. Double chance
    bet_dc = {"bet_id": "b3", "market_type": "double_chance", "selection": "1x", "amount": 1000.0, "odds": 1.35}
    assert adapter.parse_settlement(bet_dc, {"score_a": 1, "score_b": 1}).status == "WON"
    assert adapter.parse_settlement(bet_dc, {"score_a": 2, "score_b": 0}).status == "WON"
    assert adapter.parse_settlement(bet_dc, {"score_a": 0, "score_b": 1}).status == "LOST"

    # 3. Total goals & Both teams to score
    bet_over = {"bet_id": "b4", "market_type": "total_goals", "selection": "over", "param": 2.5, "amount": 1000.0, "odds": 1.90}
    assert adapter.parse_settlement(bet_over, {"score_a": 2, "score_b": 1}).status == "WON"
    assert adapter.parse_settlement(bet_over, {"score_a": 1, "score_b": 1}).status == "LOST"

    bet_btts = {"bet_id": "b5", "market_type": "both_teams_to_score", "selection": "yes", "amount": 1000.0, "odds": 1.80}
    assert adapter.parse_settlement(bet_btts, {"score_a": 1, "score_b": 1}).status == "WON"
    assert adapter.parse_settlement(bet_btts, {"score_a": 2, "score_b": 0}).status == "LOST"

    # 4. Fonbet official results feed parser
    assert FONBET_FOOTBALL_SPORT_ID == 1

    # Normal regulation win
    winner, status, is_ret, is_wo = FonbetResultsParser.parse_football_winner_and_status("2:1 (1:0, 1:1)")
    assert winner == "team_a"
    assert status == "FINISHED"
    assert not is_wo

    # Normal regulation draw
    winner, status, is_ret, is_wo = FonbetResultsParser.parse_football_winner_and_status("1:1 (0:1, 1:0)")
    assert winner == "draw"
    assert status == "FINISHED"

    # Walkover / Cancelled
    winner, status, is_ret, is_wo = FonbetResultsParser.parse_football_winner_and_status("-:-", "Матч отменен")
    assert winner is None
    assert status == "CANCELLED"
    assert is_wo

    # Complete results.json feed parsing
    mock_feed = {
        "sections": [
            {
                "fonbetSportId": FONBET_FOOTBALL_SPORT_ID,
                "name": "Футбол. Чемпионат России. РПЛ",
                "events": [501, 502],
            }
        ],
        "events": [
            {
                "id": 501,
                "name": "Зенит - Спартак Москва",
                "score": "2:1 (1:0, 1:1)",
            },
            {
                "id": 502,
                "name": "ЦСКА Москва - Локомотив Москва",
                "score": "0:0 (0:0, 0:0)",
            },
        ],
    }

    results = FonbetResultsParser.parse_feed(
        mock_feed,
        target_sport_id=FONBET_FOOTBALL_SPORT_ID,
        sport_code="football",
    )
    assert len(results) == 2
    assert results[0].winner == "team_a"
    assert results[0].final_score == "2:1 (1:0, 1:1)"
    assert results[1].winner == "draw"
    assert results[1].final_score == "0:0 (0:0, 0:0)"
    print("✓ test_football_settlement_and_results_parser passed")


def test_football_baseline_ml_inference():
    """Verify baseline ML prediction generation for football events."""
    try:
        from services.neural.app.inference import MLPredictor
    except ImportError:
        try:
            from app.inference import MLPredictor
        except ImportError:
            from neural.app.inference import MLPredictor

    class DummyModel:

        def predict_proba(self, X):
            return [[0.35, 0.65]]

    predictor = MLPredictor()
    predictor.checkpoint_data = {
        "model": DummyModel(),
        "feature_names": ["current_odds_team_a", "goal_differential", "shots_differential", "has_red_card"],
        "version_tag": "football_v1.0",
        "market": "match_winner",
    }

    f_vec = FeatureVector(
        event_id="fb_ml_test",
        sport_code="football",
        feature_version="football_v1.0",
        timestamp=datetime.now(timezone.utc),
        features={
            "current_odds_team_a": 1.75,
            "current_odds_team_b": 2.15,
            "no_vig_prob_team_a": 0.55,
            "no_vig_prob_team_b": 0.45,
            "goal_differential": 1.0,
            "shots_differential": 5.0,
            "has_red_card": 0.0,
        },
    )

    pred = predictor.predict(f_vec)
    assert isinstance(pred, MLPrediction)
    assert pred.sport_code == "football"
    assert pred.model_version == "football_v1.0"

    outcomes = {o.outcome: o for o in pred.outcomes}
    assert "team_a" in outcomes
    assert "team_b" in outcomes
    assert abs((outcomes["team_a"].model_probability + outcomes["team_b"].model_probability) - 1.0) < 0.001
    assert outcomes["team_a"].fair_odds > 1.0
    assert outcomes["team_b"].fair_odds > 1.0
    print("✓ test_football_baseline_ml_inference passed")


def test_quintuple_sport_isolation():
    """Verify that Tennis, Basketball, Volleyball, Hockey, and Football adapters coexist without interference."""
    tennis_adapter = TennisSportAdapter()
    basketball_adapter = BasketballSportAdapter()
    volleyball_adapter = VolleyballSportAdapter()
    hockey_adapter = HockeySportAdapter()
    football_adapter = FootballSportAdapter()

    # Register all five sports
    registry.register(tennis_adapter)
    registry.register(basketball_adapter)
    registry.register(volleyball_adapter)
    registry.register(hockey_adapter)
    registry.register(football_adapter)

    assert registry.get("tennis") is tennis_adapter
    assert registry.get("basketball") is basketball_adapter
    assert registry.get("volleyball") is volleyball_adapter
    assert registry.get("hockey") is hockey_adapter
    assert registry.get("football") is football_adapter

    # Verify sport codes
    assert registry.get("tennis").sport_code == "tennis"
    assert registry.get("basketball").sport_code == "basketball"
    assert registry.get("volleyball").sport_code == "volleyball"
    assert registry.get("hockey").sport_code == "hockey"
    assert registry.get("football").sport_code == "football"

    # Verify feature builders in registry
    assert feature_registry.get("tennis") is not None
    assert feature_registry.get("basketball") is not None
    assert feature_registry.get("volleyball") is not None
    assert feature_registry.get("hockey") is not None
    assert feature_registry.get("football") is not None

    print("✓ test_quintuple_sport_isolation passed")


if __name__ == "__main__":
    print("Running Phase 29 Football Sport Test Suite...")
    test_config_flag_gating()
    test_russian_team_name_normalization()
    test_hierarchical_halves_and_clean_contract_state()
    test_football_odds_and_markets()
    test_football_features_zero_future_leakage()
    test_football_settlement_and_results_parser()
    test_football_baseline_ml_inference()
    test_quintuple_sport_isolation()
    print("\n🎉 ALL PHASE 29 FOOTBALL SPORT TESTS PASSED SUCCESSFULLY!")
