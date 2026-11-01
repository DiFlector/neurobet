"""Automated test suite for Tennis Sport Adapter: normalization, hierarchy, deduplication, and persistence."""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
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
from tennis_adapter.adapter import merge_event_metadata
from tennis_adapter.pipeline import _is_stale_observation
from tennis_adapter.pipeline import _best_of_forced_by_live_score, _raw_object_matches_hash
from db.connection import SessionLocal
from db.models import Event, EventStateSnapshot, OddsSnapshot, Sport, TennisEventAttributeObservation


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


def test_source_raw_object_key_matches_upstream_digest():
    digest = "a" * 64
    assert _raw_object_matches_hash(digest, f"fonbet/tennis/2026-10-26/aaaa/{digest}.json")
    assert not _raw_object_matches_hash(digest, f"fonbet/tennis/2026-10-26/bbbb/{'b' * 64}.json")
    assert not _raw_object_matches_hash(None, f"fonbet/tennis/2026-10-26/aaaa/{digest}.json")


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


def test_event_metadata_preserves_only_explicit_best_of():
    adapter = TennisSportAdapter()
    base = {
        "id": "tn_format_1",
        "participant_a": "Игрок А",
        "participant_b": "Игрок Б",
        "best_of": 5,
    }
    assert adapter.parse_event(base).metadata["best_of"] == 5
    assert "best_of" not in adapter.parse_event({**base, "best_of": 4}).metadata
    assert "best_of" not in adapter.parse_event({**base, "best_of": 3.5}).metadata
    unknown = {key: value for key, value in base.items() if key != "best_of"}
    assert "best_of" not in adapter.parse_event(unknown).metadata


def test_prematch_event_contract_remains_scheduled_and_not_live():
    adapter = TennisSportAdapter()
    event = adapter.parse_event({
        "source_event_id": "fonbet-upcoming-1",
        "participant_a": "Пол Т", "participant_b": "Рублев А",
        "scheduled_start": "2026-10-27T12:30:00+00:00",
        "status": "prematch", "is_live": False,
    })
    assert event.status == "prematch"
    assert event.is_live is False
    assert event.scheduled_start == datetime(2026, 10, 27, 12, 30, tzinfo=timezone.utc)


def test_best_of_metadata_timestamp_is_first_observation_and_changes_on_correction():
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)
    second = datetime(2026, 1, 2, tzinfo=timezone.utc)
    digest_a, digest_b = "a" * 64, "b" * 64
    key_a = f"fonbet/tennis/2026-01-01/aaaa/{digest_a}.json"
    key_b = f"fonbet/tennis/2026-01-02/bbbb/{digest_b}.json"
    initial = merge_event_metadata({}, {
        "best_of": 3, "best_of_source_content_sha256": digest_a,
        "best_of_source_raw_object_key": key_a,
    }, first)
    assert initial["best_of_available_at"] == first.isoformat()
    repeated = merge_event_metadata(initial, {
        "best_of": 3, "best_of_source_content_sha256": digest_b,
        "best_of_source_raw_object_key": key_b,
    }, second)
    assert repeated["best_of_available_at"] == first.isoformat()
    assert repeated["best_of_source_content_sha256"] == digest_a
    assert repeated["best_of_source_raw_object_key"] == key_a
    corrected = merge_event_metadata(repeated, {
        "best_of": 5, "best_of_source_content_sha256": digest_b,
        "best_of_source_raw_object_key": key_b,
    }, second)
    assert corrected["best_of_available_at"] == second.isoformat()
    assert corrected["best_of_source_content_sha256"] == digest_b
    assert corrected["best_of_source_raw_object_key"] == key_b
    unproved_correction = merge_event_metadata(repeated, {"best_of": 5}, second)
    assert "best_of_source_content_sha256" not in unproved_correction
    assert "best_of_source_raw_object_key" not in unproved_correction
    assert "best_of_available_at" not in merge_event_metadata(
        {}, {"surface": "hard"}, second
    )


def test_live_score_infers_only_format_forced_by_play():
    adapter = TennisSportAdapter()
    one_all = adapter.parse_state({
        "id": "format-one-all", "score_state": {
            "raw_score_string": "1-1 (6-4, 4-6, 2-1)",
        },
    })
    assert one_all.current_period == 3
    assert _best_of_forced_by_live_score(one_all) is None

    two_all = adapter.parse_state({
        "id": "format-two-all", "score_state": {
            "raw_score_string": "2-2 (6-4, 4-6, 6-3, 3-6, 1-0)",
        },
    })
    assert two_all.current_period == 5
    assert _best_of_forced_by_live_score(two_all) == 5

    third_set = adapter.parse_state({
        "id": "format-third-set", "score_state": {
            "raw_score_string": "1-1 (6-4, 4-6, 2-1)",
        },
    })
    assert _best_of_forced_by_live_score(third_set) is None


def test_replayed_snapshot_cannot_rewind_event_watermark():
    last_seen = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    assert _is_stale_observation(datetime(2026, 10, 26, 11, tzinfo=timezone.utc), last_seen)
    assert not _is_stale_observation(last_seen, last_seen)
    assert not _is_stale_observation(datetime(2026, 10, 26, 13, tzinfo=timezone.utc), last_seen)


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
        sport_created = session.query(Sport).filter(Sport.code == "tennis").one_or_none() is None
        if sport_created:
            session.add(Sport(code="tennis", name="Tennis", status="active", is_primary=True, metadata_json={}))
            session.flush()
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

        # A live score that is impossible in best-of-three proves best-of-five
        # at this observation time. Replaying the same source payload is idempotent.
        score_payload = dict(raw_event_payload)
        score_payload["score_state"] = {
            "raw_score_string": "2-2 (6-4, 4-6, 6-3, 3-6, 1-0)",
            "current_game_a": 1,
            "current_game_b": 0,
            "server": "player_a",
        }
        observed_at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=1)
        source_payload_hash = "sha256:" + ("a" * 64)
        source_payload_raw_key = f"fonbet/tennis/test/{'a' * 4}/{'a' * 64}.json"
        pipeline.process_and_persist(
            session, score_payload, observed_at=observed_at,
            source_payload_hash=source_payload_hash,
            source_payload_raw_key=source_payload_raw_key,
        )
        pipeline.process_and_persist(
            session, score_payload, observed_at=observed_at,
            source_payload_hash=source_payload_hash,
            source_payload_raw_key=source_payload_raw_key,
        )
        format_rows = (
            session.query(TennisEventAttributeObservation)
            .filter(
                TennisEventAttributeObservation.event_id == db_event.id,
                TennisEventAttributeObservation.attribute_name == "best_of",
            )
            .all()
        )
        assert len(format_rows) == 1
        assert format_rows[0].value_json == {"value": 5}
        assert format_rows[0].available_at == observed_at
        assert format_rows[0].source == "fonbet_score_invariant"
        assert format_rows[0].source_content_hash
        assert format_rows[0].raw_object_key == source_payload_raw_key
        assert format_rows[0].evidence_hash
        source_state = (
            session.query(EventStateSnapshot)
            .filter(EventStateSnapshot.event_id == db_event.id,
                    EventStateSnapshot.observed_at == observed_at)
            .order_by(EventStateSnapshot.id.desc())
            .first()
        )
        assert source_state.raw_hash == "a" * 64
        assert source_state.raw_object_key == source_payload_raw_key

        # A stored object key without the collector's digest is not enough to
        # claim that a projection hash proves the archived source bytes.
        unhashed_at = observed_at + timedelta(minutes=1)
        pipeline.process_and_persist(
            session, score_payload, observed_at=unhashed_at,
            source_payload_raw_key=source_payload_raw_key,
        )
        unhashed_state = (
            session.query(EventStateSnapshot)
            .filter(EventStateSnapshot.event_id == db_event.id,
                    EventStateSnapshot.observed_at == unhashed_at)
            .order_by(EventStateSnapshot.id.desc())
            .first()
        )
        assert unhashed_state.raw_hash is None
        assert unhashed_state.raw_object_key == source_payload_raw_key
        assert not session.query(TennisEventAttributeObservation).filter(
            TennisEventAttributeObservation.event_id == db_event.id,
            TennisEventAttributeObservation.attribute_name == "best_of",
            TennisEventAttributeObservation.available_at == unhashed_at,
        ).first()

        # A late replay preserves its original source time in the immutable
        # history while leaving the current event projection untouched.
        stale_at = observed_at - timedelta(days=1)
        pipeline.process_and_persist(
            session, score_payload, observed_at=stale_at,
            source_payload_hash=source_payload_hash,
            source_payload_raw_key=source_payload_raw_key,
        )
        pipeline.process_and_persist(
            session, score_payload, observed_at=stale_at,
            source_payload_hash=source_payload_hash,
            source_payload_raw_key=source_payload_raw_key,
        )
        format_rows = (
            session.query(TennisEventAttributeObservation)
            .filter(
                TennisEventAttributeObservation.event_id == db_event.id,
                TennisEventAttributeObservation.attribute_name == "best_of",
            )
            .order_by(TennisEventAttributeObservation.available_at)
            .all()
        )
        assert len(format_rows) == 2
        assert format_rows[0].available_at == stale_at
        assert format_rows[0].source_content_hash
        assert format_rows[0].raw_object_key == source_payload_raw_key
        session.refresh(db_event)
        assert db_event.metadata_json["best_of_available_at"] == observed_at.isoformat()

        # Cleanup test records
        session.query(TennisEventAttributeObservation).filter(
            TennisEventAttributeObservation.event_id == db_event.id
        ).delete()
        session.query(OddsSnapshot).filter(OddsSnapshot.event_id == db_event.id).delete()
        session.query(EventStateSnapshot).filter(EventStateSnapshot.event_id == db_event.id).delete()
        session.query(Event).filter(Event.id == db_event.id).delete()
        if sport_created:
            session.query(Sport).filter(Sport.code == "tennis").delete()
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
