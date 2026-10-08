"""Automated test suite for Tennis Sport Adapter: normalization, hierarchy, deduplication, and persistence."""

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add package paths
sys.path.insert(0, "/srv/neurobet/packages/contracts/src")
sys.path.insert(0, "/srv/neurobet/packages/db/src")
sys.path.insert(0, "/srv/neurobet/packages/sports-core/src")
sys.path.insert(0, "/srv/neurobet/sports/tennis/src")

from sports_core import registry
from tennis_adapter import (
    TennisSportAdapter,
    TennisPipeline,
    detect_tennis_surface,
    normalize_russian_player_name,
)
from db.connection import SessionLocal
from db.models import Event, EventStateSnapshot, OddsSnapshot


def test_registry_and_surface_detection():
    """Verify sport registry and surface classification."""
    adapter = registry.get("tennis")
    assert adapter is not None
    assert adapter.sport_code == "tennis"
    assert "match_winner" in adapter.supported_markets()

    # Surface classification tests
    assert detect_tennis_surface("ATP. Мастерс 1000. Рим (Грунт)") == "clay"
    assert detect_tennis_surface("Уимблдон. Одиночный разряд (Трава)") == "grass"
    assert detect_tennis_surface("ATP. Шанхай (Хард)") == "hard"
    assert detect_tennis_surface("Неизвестный турнир") == "hard"  # default
    print("test_registry_and_surface_detection: OK")


def test_russian_player_name_normalization():
    """Verify Russian tennis player name normalization and alias resolution."""
    assert normalize_russian_player_name("Медведев Д.") == "Даниил Медведев"
    assert normalize_russian_player_name("Д. Медведев") == "Даниил Медведев"
    assert normalize_russian_player_name("[WC] Джокович Н. (1)") == "Новак Джокович"
    assert normalize_russian_player_name("Алькарас К.") == "Карлос Алькарас"
    assert normalize_russian_player_name("Синнер Я.") == "Янник Синнер"
    assert normalize_russian_player_name("Рублёв А.") == "Андрей Рублев"
    assert normalize_russian_player_name("Зверев А.") == "Александр Зверев"
    assert normalize_russian_player_name("Соболенко А.") == "Арина Соболенко"
    print("test_russian_player_name_normalization: OK")


def test_hierarchical_state_and_score():
    """
    Verify hierarchical tennis state parsing:
    match -> set -> game -> point, server, break point.
    """
    adapter = TennisSportAdapter()
    raw_match = {
        "id": "tn_test_101",
        "participant_a": "Медведев Д.",
        "participant_b": "Синнер Я.",
        "tournament": "ATP. Мастерс 1000. Шанхай (Хард)",
        "score_state": {
            "raw_score_string": "1-1 (6-4, 3-6, 4-3*)",
            "current_game_a": 4,
            "current_game_b": 3,
            "points_a": "40",
            "points_b": "30",
            "server": "player_a",
        },
        "stats": {
            "aces_a": 7,
            "aces_b": 5,
            "double_faults_a": 2,
            "double_faults_b": 1,
            "first_serve_win_pct_a": 0.78,
            "first_serve_win_pct_b": 0.72,
        },
    }

    state = adapter.parse_state(raw_match)
    assert state.sport_code == "tennis"
    assert state.current_period == 3  # 3rd set active
    assert len(state.sets) == 2        # 2 completed sets
    assert state.sets[0].games_a == 6
    assert state.sets[0].games_b == 4
    assert state.sets[1].games_a == 3
    assert state.sets[1].games_b == 6
    assert state.server == "player_a"
    assert state.current_game.points_a == "40"
    assert state.current_game.points_b == "30"
    assert state.stats["aces_a"] == 7
    print("test_hierarchical_state_and_score: OK")


def test_odds_snapshot_and_mvp_market():
    """Verify odds snapshot extraction with MVP match_winner market."""
    adapter = TennisSportAdapter()
    raw_match = {
        "id": "tn_test_102",
        "participant_a": "Медведев Д.",
        "participant_b": "Синнер Я.",
        "odds_a": 2.10,
        "odds_b": 1.75,
    }

    odds = adapter.parse_odds(raw_match)
    assert len(odds.markets) == 1
    market = odds.markets[0]
    assert market.market_type == "match_winner"
    assert market.name == "Победитель матча"
    assert len(market.selections) == 2
    assert market.selections[0].outcome == "player_a"
    assert market.selections[0].name == "Даниил Медведев"
    assert market.selections[0].odds == 2.10
    assert market.selections[0].probability_implied == round(1.0 / 2.10, 4)
    assert odds.content_hash.startswith("sha256:")
    print("test_odds_snapshot_and_mvp_market: OK")


def test_pipeline_raw_to_event_state_odds_and_deduplication():
    """
    Acceptance Criteria:
    1. Один реальный/сохраненный live event корректно проходит raw → event → state → odds.
    2. Повторный snapshot не создает ложные изменения (deduplication).
    3. Изменение odds создает новую историческую запись.
    """
    test_id = f"test_{uuid.uuid4().hex[:8]}"
    raw_event_payload = {
        "source_event_id": test_id,
        "participant_a": "Алькарас К.",
        "participant_b": "Джокович Н.",
        "tournament": "Уимблдон 2026. Финал (Трава)",
        "score_state": {
            "raw_score_string": "1-0 (6-4, 2-1*)",
            "current_game_a": 2,
            "current_game_b": 1,
            "points_a": "15",
            "points_b": "0",
            "server": "player_a",
        },
        "odds_a": 1.65,
        "odds_b": 2.25,
    }

    pipeline = TennisPipeline()

    with SessionLocal() as session:
        # Step 1: Initial ingestion (raw -> event -> state -> odds)
        event, state, odds_1, changed_1 = pipeline.process_and_persist(session, raw_event_payload)
        assert event.participant_a == "Карлос Алькарас"
        assert event.participant_b == "Новак Джокович"
        assert event.metadata["surface"] == "grass"
        assert state.server == "player_a"
        assert changed_1 is True

        # Verify DB records
        db_event = session.query(Event).filter(Event.source_event_id == test_id).one()
        assert db_event.participant_a_name == "Карлос Алькарас"
        assert db_event.is_live is True

        odds_rows_1 = session.query(OddsSnapshot).filter(OddsSnapshot.event_id == db_event.id).count()
        assert odds_rows_1 == 2  # 2 selections (player_a, player_b)

        # Step 2: Ingest identical snapshot (odds unchanged)
        # MUST NOT create a redundant odds row (deduplication)
        _, _, odds_2, changed_2 = pipeline.process_and_persist(session, raw_event_payload)
        assert changed_2 is False, "Unchanged odds must not trigger odds change"
        odds_rows_2 = session.query(OddsSnapshot).filter(OddsSnapshot.event_id == db_event.id).count()
        assert odds_rows_2 == 2, "Duplicate snapshot must NOT create false DB rows"

        # Step 3: Ingest updated snapshot with shifted odds (e.g. odds_a moves to 1.80)
        # MUST create a new historical record in TimescaleDB hypertable
        changed_payload = dict(raw_event_payload)
        changed_payload["odds_a"] = 1.80
        changed_payload["odds_b"] = 2.05

        _, _, odds_3, changed_3 = pipeline.process_and_persist(session, changed_payload)
        assert changed_3 is True, "Shifted odds must trigger new snapshot"
        odds_rows_3 = session.query(OddsSnapshot).filter(OddsSnapshot.event_id == db_event.id).count()
        assert odds_rows_3 == 4, "Odds change must create a new historical record"

        # Cleanup test records
        session.query(OddsSnapshot).filter(OddsSnapshot.event_id == db_event.id).delete()
        session.query(EventStateSnapshot).filter(EventStateSnapshot.event_id == db_event.id).delete()
        session.query(Event).filter(Event.id == db_event.id).delete()
        session.commit()

    print("test_pipeline_raw_to_event_state_odds_and_deduplication: OK")


def test_settlement_parser():
    """Verify match conclusion and settlement result parser."""
    adapter = TennisSportAdapter()
    finished_match = {
        "id": "tn_test_103",
        "participant_a": "Медведев Д.",
        "participant_b": "Синнер Я.",
        "status": "finished",
        "winner": "player_a",
        "final_score": "2-1 (6-4, 4-6, 6-3)",
        "winning_odds": 2.10,
    }

    settlement = adapter.parse_result(finished_match, bet_id="bet_103", stake=100.0)
    assert settlement is not None
    assert settlement.status == "WON"
    assert settlement.payout == 210.0
    assert settlement.net_profit == 110.0
    assert "Winner: player_a" in settlement.settlement_reason
    print("test_settlement_parser: OK")


if __name__ == "__main__":
    test_registry_and_surface_detection()
    test_russian_player_name_normalization()
    test_hierarchical_state_and_score()
    test_odds_snapshot_and_mvp_market()
    test_pipeline_raw_to_event_state_odds_and_deduplication()
    test_settlement_parser()
    print("\nALL TENNIS ADAPTER TESTS PASSED SUCCESSFULLY!")
