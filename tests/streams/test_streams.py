"""Automated tests for Redis Streams, consumer groups, idempotency, retries, and dead-letter handling."""

import os
import uuid
from datetime import datetime, timezone
import redis

from streams import (
    ALL_CORE_STREAMS,
    ALL_STREAMS,
    STREAM_DEAD_LETTER,
    STREAM_FONBET_EVENTS,
    STREAM_FONBET_RAW,
    STREAM_ML_PREDICTIONS,
    MessageEnvelope,
    StreamConsumerGroup,
    StreamPublisher,
)

REDIS_URL = os.getenv("REDIS_URL")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_INTERNAL_PORT", os.getenv("REDIS_PORT", "6379")))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "Nb_Rd_3vF8nK2pW7sY1tZ4mQ6bC9eH5jL")


def get_redis_client():
    if REDIS_URL:
        client = redis.from_url(REDIS_URL, decode_responses=True)
    else:
        client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD if REDIS_PASSWORD else None,
            decode_responses=True,
        )
    client.ping()
    return client


def test_streams_constants_definition():
    """Verify all 15 canonical streams plus dead-letter queue are defined."""
    assert len(ALL_CORE_STREAMS) == 15
    assert len(ALL_STREAMS) == 16
    assert STREAM_DEAD_LETTER in ALL_STREAMS
    assert "fonbet.raw" in ALL_STREAMS
    assert "bet.validated" in ALL_STREAMS
    assert "training.jobs" in ALL_STREAMS
    print("test_streams_constants_definition: OK")


def test_message_envelope_serialization():
    """Verify MessageEnvelope serialization to Redis dict and deserialization."""
    payload = {"match_id": "tennis_123", "score": "6-4, 3-2", "live": True}
    idem_key = f"idem_{uuid.uuid4().hex}"
    envelope = MessageEnvelope(
        stream_name=STREAM_FONBET_RAW,
        idempotency_key=idem_key,
        payload=payload,
        producer="collector-service",
    )

    redis_dict = envelope.to_redis_dict()
    assert redis_dict["stream_name"] == STREAM_FONBET_RAW
    assert redis_dict["idempotency_key"] == idem_key
    assert redis_dict["producer"] == "collector-service"
    assert '"match_id": "tennis_123"' in redis_dict["payload"]

    # Reconstruct
    reconstructed = MessageEnvelope.from_redis_dict(redis_dict, stream_name=STREAM_FONBET_RAW)
    assert reconstructed.stream_name == STREAM_FONBET_RAW
    assert reconstructed.idempotency_key == idem_key
    assert reconstructed.payload["match_id"] == "tennis_123"
    assert reconstructed.payload["live"] is True
    assert reconstructed.retry_count == 0
    print("test_message_envelope_serialization: OK")


def test_stream_publish_and_consume(client):
    """Verify publishing to a stream and consuming via Consumer Group."""
    test_stream = f"test.stream.{uuid.uuid4().hex[:8]}"
    group_name = "test_cg_primary"
    consumer_name = "worker_1"

    publisher = StreamPublisher(client, producer="test_publisher")
    consumer = StreamConsumerGroup(client, test_stream, group_name, consumer_name)
    consumer.create_group(start_id="0")

    received_payloads = []

    def handler(env: MessageEnvelope):
        received_payloads.append(env.payload)

    # Publish message
    test_payload = {"event_id": "tennis_456", "sport": "tennis"}
    idem_key = f"key_{uuid.uuid4().hex}"
    publisher.publish(test_stream, test_payload, idempotency_key=idem_key)

    # Process message
    processed = consumer.process_one(handler, count=10, block_ms=2000)
    assert processed == 1
    assert len(received_payloads) == 1
    assert received_payloads[0]["event_id"] == "tennis_456"

    # Cleanup
    client.delete(test_stream)
    print("test_stream_publish_and_consume: OK")


def test_idempotency_prevents_duplicate_actions(client):
    """
    Acceptance Criteria:
    Один message не создает два одинаковых database action при повторной доставке.
    """
    test_stream = f"test.stream.idem.{uuid.uuid4().hex[:8]}"
    group_name = "test_cg_idem"
    consumer_name = "worker_idem"

    consumer = StreamConsumerGroup(client, test_stream, group_name, consumer_name)
    consumer.create_group(start_id="0")
    publisher = StreamPublisher(client)

    action_executions = []

    def database_action_handler(env: MessageEnvelope):
        action_executions.append(env.payload["amount"])

    idem_key = f"idem_dup_test_{uuid.uuid4().hex}"

    # Publish message 1 with the idempotency key
    publisher.publish(test_stream, {"amount": 5000}, idempotency_key=idem_key)
    # Publish message 2 with the exact SAME idempotency key (simulating re-delivery / retry)
    publisher.publish(test_stream, {"amount": 5000}, idempotency_key=idem_key)

    # Process first message
    processed_first = consumer.process_one(database_action_handler, count=1, block_ms=2000)
    assert processed_first == 1
    assert len(action_executions) == 1

    # Process second duplicate message
    processed_second = consumer.process_one(database_action_handler, count=1, block_ms=2000)
    # The second message should be recognized as duplicate, skipped, and NOT execute the handler!
    assert processed_second == 0
    assert len(action_executions) == 1  # Action executed exactly once!

    # Cleanup
    client.delete(test_stream)
    client.delete(f"idempotency:{idem_key}")
    print("test_idempotency_prevents_duplicate_actions: OK")


def test_worker_error_and_dead_letter_routing(client):
    """
    Acceptance Criteria:
    Ошибка worker не теряет message. Сообщение после max retries попадает в Dead-Letter Queue.
    """
    test_stream = f"test.stream.fail.{uuid.uuid4().hex[:8]}"
    group_name = "test_cg_fail"
    consumer_name = "worker_fail"

    consumer = StreamConsumerGroup(client, test_stream, group_name, consumer_name)
    consumer.create_group(start_id="0")
    publisher = StreamPublisher(client)

    # Max retries = 1 for fast test
    envelope = MessageEnvelope(
        stream_name=test_stream,
        idempotency_key=f"fail_{uuid.uuid4().hex}",
        payload={"task": "critical_calc", "value": -1},
        max_retries=1,
    )
    publisher.publish_envelope(envelope)

    attempts = []

    def failing_handler(env: MessageEnvelope):
        attempts.append(env.retry_count)
        raise ValueError("Simulated processing error in worker")

    # 1st attempt: retry_count=0 -> should fail and increment retry to 1, republish
    consumer.process_one(failing_handler, count=1, block_ms=2000)
    assert len(attempts) == 1

    # 2nd attempt: retry_count=1 -> exceeds max_retries(1), should route to dead-letter queue!
    consumer.process_one(failing_handler, count=1, block_ms=2000)
    assert len(attempts) == 2

    # Check dead letter stream
    dl_messages = client.xrevrange(STREAM_DEAD_LETTER, count=10)
    assert len(dl_messages) > 0

    found_in_dlq = False
    for msg_id, fields in dl_messages:
        if fields.get("idempotency_key") == envelope.idempotency_key:
            found_in_dlq = True
            assert fields.get("original_stream") == test_stream
            assert int(fields.get("retry_count")) >= 1
            assert "Simulated processing error in worker" in fields.get("error_context", "")
            break

    assert found_in_dlq is True, "Failed message must be safely preserved in Dead Letter Queue"

    # Cleanup
    client.delete(test_stream)
    print("test_worker_error_and_dead_letter_routing: OK")


if __name__ == "__main__":
    test_streams_constants_definition()
    test_message_envelope_serialization()
    client = get_redis_client()
    try:
        test_stream_publish_and_consume(client)
        test_idempotency_prevents_duplicate_actions(client)
        test_worker_error_and_dead_letter_routing(client)
        print("\nALL REDIS STREAMS TESTS PASSED SUCCESSFULLY!")
    finally:
        client.close()
