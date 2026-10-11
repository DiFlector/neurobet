"""Redis Streams Load and High-Throughput Messaging Benchmark."""

import json
import os
import sys
import time
import uuid
import redis

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


def test_redis_streams_load():
    """
    Stress tests Redis Streams:
    1. High-throughput pipelined XADD: 2,000 messages.
    2. Measures write throughput (msgs/sec) and latency.
    3. Consumer group creation and batch XREADGROUP reading with XACK.
    4. Measures read throughput and consumer latency.
    5. Cleanup of benchmark stream.
    """
    print("\n--- Running Redis Streams Load Test ---")
    client = get_redis_client()
    stream_name = f"bench:stream:{uuid.uuid4().hex[:8]}"
    group_name = "bench_consumer_group"
    num_messages = 2000

    try:
        # 1. Pipelined XADD benchmark
        t0 = time.perf_counter()
        pipe = client.pipeline(transaction=False)
        for i in range(num_messages):
            msg_payload = {
                "event_id": f"evt_bench_{i}",
                "sport_code": "football",
                "score": f"{i % 3}:{i % 2}",
                "timestamp": time.time(),
                "data": json.dumps({"minute": 45 + (i % 45), "odds": 1.95}),
            }
            pipe.xadd(stream_name, msg_payload)
        pipe.execute()

        write_elapsed = time.perf_counter() - t0
        write_throughput = num_messages / write_elapsed
        avg_write_latency_ms = (write_elapsed / num_messages) * 1000

        print(f"XADD Write Benchmark: {num_messages} messages in {write_elapsed:.3f} s")
        print(f"Write Throughput: {write_throughput:.1f} msgs/sec")
        print(f"Average Write Latency: {avg_write_latency_ms:.3f} ms/msg")

        assert write_throughput > 2000.0, f"Write throughput {write_throughput:.1f} below 2,000 msgs/sec SLA"

        # 2. Setup consumer group
        client.xgroup_create(stream_name, group_name, id="0", mkstream=True)

        # 3. Batch reading with XREADGROUP and XACK
        t_read_start = time.perf_counter()
        read_count = 0
        batch_size = 200

        while read_count < num_messages:
            entries = client.xreadgroup(
                groupname=group_name,
                consumername="worker_1",
                streams={stream_name: ">"},
                count=batch_size,
            )
            if not entries:
                break

            msg_ids = []
            for stream_key, msgs in entries:
                for msg_id, fields in msgs:
                    msg_ids.append(msg_id)
                    read_count += 1

            if msg_ids:
                client.xack(stream_name, group_name, *msg_ids)

        read_elapsed = time.perf_counter() - t_read_start
        read_throughput = read_count / read_elapsed
        avg_read_latency_ms = (read_elapsed / read_count) * 1000

        print(f"XREADGROUP Read & ACK Benchmark: {read_count} messages in {read_elapsed:.3f} s")
        print(f"Read Throughput: {read_throughput:.1f} msgs/sec")
        print(f"Average Read Latency: {avg_read_latency_ms:.3f} ms/msg")

        assert read_count == num_messages
        assert read_throughput > 1500.0, f"Read throughput {read_throughput:.1f} below 1,500 msgs/sec SLA"

        print("✓ Redis Streams high-throughput load test passed successfully!")

    finally:
        client.delete(stream_name)


if __name__ == "__main__":
    test_redis_streams_load()
