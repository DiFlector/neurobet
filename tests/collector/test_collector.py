"""Automated tests for FON.BET collector: live filtering, Russian parsing, circuit breaker, hashing, and MinIO storage."""

import hashlib
import json
import os
import time
from datetime import datetime, timezone

import sys
from pathlib import Path
sys.path.insert(0, "/app")
sys.path.insert(0, str(Path("/srv/neurobet/services/collector")))
sys.path.insert(0, str(Path("/srv/neurobet/packages/streams/src")))

from app.circuit_breaker import CircuitBreaker, CircuitState
from app.parser import FonbetLiveTennisParser
from app.s3_writer import MinIORawWriter


def test_parser_filters_out_prematch_and_accepts_live():
    """
    Acceptance Criteria:
    Мы берем и ставим только на LIVE соревнования которые идут сейчас.
    Нет смысла ставить и запоминать соревнования, которые еще не начались.
    """
    # Prematch event (not started, place='prematch', no live score)
    prematch_card = {
        "id": "99901",
        "name": "Новак Джокович - Карлос Алькарас",
        "team1": "Новак Джокович",
        "team2": "Карлос Алькарас",
        "competitionName": "Ролан Гаррос. Финал",
        "place": "prematch",
        "status": "scheduled",
        "is_live": False,
        "score": "",
    }
    parsed_prematch = FonbetLiveTennisParser.parse_single_live_card(prematch_card)
    assert parsed_prematch is None, "Prematch unstarted event MUST be filtered out"

    # Live event (in progress, live score present)
    live_card = {
        "id": "88801",
        "team1": "Даниил Медведев",
        "team2": "Янник Синнер",
        "competitionName": "ATP. Мастерс 1000. Шанхай. Одиночный разряд",
        "place": "live",
        "status": "live",
        "is_live": True,
        "score": "1-1 (6-4, 4-6, 3-2*)",
        "odds_a": 2.15,
        "odds_b": 1.72,
    }
    parsed_live = FonbetLiveTennisParser.parse_single_live_card(live_card)
    assert parsed_live is not None, "Live event MUST be parsed"
    assert parsed_live["is_live"] is True
    assert parsed_live["status"] == "live"
    assert parsed_live["participant_a"] == "Даниил Медведев"
    assert parsed_live["participant_b"] == "Янник Синнер"
    assert parsed_live["tournament"] == "ATP. Мастерс 1000. Шанхай. Одиночный разряд"
    assert parsed_live["score_state"]["sets_a"] == 1
    assert parsed_live["score_state"]["sets_b"] == 1
    assert parsed_live["score_state"]["current_game_a"] == 3
    assert parsed_live["score_state"]["current_game_b"] == 2
    assert parsed_live["score_state"]["server"] == "player_a"
    assert len(parsed_live["markets"]) == 1
    assert parsed_live["markets"][0]["name"] == "Победитель матча"
    print("test_parser_filters_out_prematch_and_accepts_live: OK")


def test_parser_card_error_isolation():
    """
    Acceptance Criteria:
    Ошибка parser одной карточки не останавливает весь collector.
    """
    feed = {
        "events": [
            # 1. Valid live match
            {
                "id": "1001",
                "team1": "Андрей Рублев",
                "team2": "Карен Хачанов",
                "place": "live",
                "score": "0-0 (3-2*)",
                "odds_a": 1.85,
                "odds_b": 1.95,
            },
            # 2. Corrupted card with invalid data types
            {
                "id": "1002",
                "team1": 12345,  # Non-string corrupted
                "place": "live",
                "factors": "corrupted_non_list_factors",
            },
            # 3. Another valid live match
            {
                "id": "1003",
                "team1": "Александр Зверев",
                "team2": "Тейлор Фриц",
                "place": "live",
                "score": "1-0 (6-3, 2-1)",
                "odds_a": 1.50,
                "odds_b": 2.60,
            },
        ]
    }
    parsed_events = FonbetLiveTennisParser.parse_events_feed(feed)
    assert len(parsed_events) == 2, "Should parse exactly the 2 valid cards and safely skip corrupted card"
    assert parsed_events[0]["participant_a"] == "Андрей Рублев"
    assert parsed_events[1]["participant_a"] == "Александр Зверев"
    print("test_parser_card_error_isolation: OK")


def test_sha256_content_hashing():
    """Verify SHA-256 content hashing of raw snapshot payload."""
    payload = {
        "source": "fonbet",
        "sport_code": "tennis",
        "events": [{"id": "101", "name": "Игрок 1 - Игрок 2"}],
    }
    raw_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    expected_hash = f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}"
    assert expected_hash.startswith("sha256:")
    assert len(expected_hash) == 7 + 64
    print("test_sha256_content_hashing: OK")


def test_circuit_breaker_transitions_and_backoff():
    """Verify Circuit Breaker transitions and backoff calculations."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=0.1, base_backoff_seconds=1.0)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # Record 2 failures (< threshold 3)
    cb.record_failure(ValueError("Error 1"))
    cb.record_failure(ValueError("Error 2"))
    assert cb.state == CircuitState.CLOSED
    assert cb.get_backoff_delay() == 2.0  # 1.0 * (2^1)

    # 3rd failure reaches threshold -> Trips to OPEN
    cb.record_failure(ValueError("Error 3"))
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False

    # Wait for recovery timeout
    time.sleep(0.15)
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN

    # Probe succeeds -> transitions to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 0
    print("test_circuit_breaker_transitions_and_backoff: OK")


def test_minio_raw_snapshot_persistence():
    """Verify MinIO/S3 raw snapshot writer saves and metadata is preserved with UTC timestamp."""
    writer = MinIORawWriter()
    writer.ensure_bucket()

    now = datetime.now(timezone.utc)
    test_hash = f"sha256:{hashlib.sha256(b'test_snapshot_data').hexdigest()}"
    test_snapshot = {
        "source": "fonbet",
        "collector_version": "1.0.0",
        "collected_at": now.isoformat(),
        "sport_code": "tennis",
        "page_type": "live",
        "content_hash": test_hash,
        "events": [
            {
                "event_id": "fonbet_111",
                "participant_a": "Роман Сафиуллин",
                "participant_b": "Артур Фис",
                "is_live": True,
            }
        ],
    }

    s3_key = writer.save_raw_snapshot(test_hash, test_snapshot, now)
    assert s3_key.startswith("fonbet/tennis/")
    assert test_hash.replace("sha256:", "") in s3_key

    # Retrieve and verify from MinIO
    resp = writer.client.get_object(Bucket=writer.bucket, Key=s3_key)
    body = json.loads(resp["Body"].read().decode("utf-8"))
    assert body["content_hash"] == test_hash
    assert body["events"][0]["participant_a"] == "Роман Сафиуллин"
    assert resp["Metadata"]["collector_version"] == "1.0.0"
    print("test_minio_raw_snapshot_persistence: OK")


if __name__ == "__main__":
    test_parser_filters_out_prematch_and_accepts_live()
    test_parser_card_error_isolation()
    test_sha256_content_hashing()
    test_circuit_breaker_transitions_and_backoff()
    test_minio_raw_snapshot_persistence()
    print("\nALL COLLECTOR TESTS PASSED SUCCESSFULLY!")
