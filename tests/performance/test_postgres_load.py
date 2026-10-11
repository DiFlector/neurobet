"""PostgreSQL and TimescaleDB Load & Query Throughput Benchmark."""

import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

# Ensure package paths are present
paths = [
    "/srv/neurobet/packages/contracts/src",
    "/srv/neurobet/packages/db/src",
    "/app/packages/contracts/src",
    "/app/packages/db/src",
    "/app",
]
for p in paths:
    if p not in sys.path:
        sys.path.insert(0, p)

from sqlalchemy import select, desc, text
from db.connection import SessionLocal
from db.models import (
    Event as DBEvent,
    EventStateSnapshot as DBEventStateSnapshot,
    OddsSnapshot as DBOddsSnapshot,
)


def test_postgres_load_and_hypertables():
    """
    Stress tests PostgreSQL & TimescaleDB hypertables:
    1. Bulk batch insertion of 1,000 snapshots.
    2. High-frequency point-in-time range queries with composite indexes.
    3. Measures QPS, p50, p95, and p99 latency percentiles.
    4. Verifies TimescaleDB hypertable compression configuration.
    """
    print("\n--- Running PostgreSQL & TimescaleDB Load Test ---")
    session = SessionLocal()

    try:
        # 1. Verify TimescaleDB compression is active on all hypertables
        comp_res = session.execute(
            text("SELECT hypertable_name, compression_enabled FROM timescaledb_information.hypertables;")
        ).fetchall()
        comp_map = {r[0]: r[1] for r in comp_res}
        print(f"TimescaleDB Hypertables Compression: {comp_map}")
        assert comp_map.get("event_state_snapshots") is True, "event_state_snapshots must have compression enabled"
        assert comp_map.get("odds_snapshots") is True, "odds_snapshots must have compression enabled"
        assert comp_map.get("feature_snapshots") is True, "feature_snapshots must have compression enabled"

        # 2. Ensure sports are registered in sports table
        session.execute(text("""
            INSERT INTO sports (code, name, status, is_primary) VALUES
                ('basketball', 'Баскетбол', 'active', false),
                ('volleyball', 'Волейбол', 'active', false),
                ('hockey', 'Хоккей', 'active', false),
                ('football', 'Футбол', 'active', false)
            ON CONFLICT (code) DO NOTHING;
        """))
        session.commit()


        # 3. Setup synthetic test event
        test_event_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        event = DBEvent(
            id=test_event_id,
            source="benchmark",
            source_event_id=f"bench_{uuid.uuid4().hex[:8]}",
            sport_code="football",
            participant_a_name="Club Alpha",

            participant_b_name="Club Beta",
            scheduled_start_at=now,
            first_seen_at=now,
            last_seen_at=now,
            status="live",
            is_live=True,
            current_period=1,
            current_score={"score_a": 1, "score_b": 0},
            metadata_json={"tournament": "Performance Tournament", "surface": "grass"},
        )

        session.add(event)
        session.commit()

        # 3. High-volume bulk insert: 1,000 state snapshots
        num_records = 1000
        snapshots = []
        for i in range(num_records):
            obs_time = now - timedelta(seconds=num_records - i)
            s = DBEventStateSnapshot(
                id=uuid.uuid4(),
                observed_at=obs_time,
                event_id=test_event_id,
                source_event_id=event.source_event_id,
                sport_code="football",
                status="live",
                current_period=1,
                match_clock_seconds=i,
                score=f"{i % 3}:{i % 2}",
                score_detail={"minute": i, "score_a": i % 3, "score_b": i % 2},
            )
            snapshots.append(s)

        t0 = time.perf_counter()
        session.bulk_save_objects(snapshots)
        session.commit()
        insert_elapsed = time.perf_counter() - t0
        insert_qps = num_records / insert_elapsed
        print(f"Bulk Insert: {num_records} rows in {insert_elapsed:.3f} s ({insert_qps:.1f} rows/sec)")
        assert insert_qps > 500.0, f"Insert throughput {insert_qps:.1f} rows/sec below SLA (500 rows/sec)"

        # 4. Point-in-time query benchmark (500 queries)
        num_queries = 500
        latencies = []
        query_start = time.perf_counter()

        for j in range(num_queries):
            cutoff = now - timedelta(seconds=j * 2)
            q_t0 = time.perf_counter()
            stmt = (
                select(DBEventStateSnapshot)
                .where(
                    DBEventStateSnapshot.event_id == test_event_id,
                    DBEventStateSnapshot.observed_at <= cutoff,
                )
                .order_by(desc(DBEventStateSnapshot.observed_at))
                .limit(1)
            )
            row = session.execute(stmt).scalar_one_or_none()
            q_elapsed = (time.perf_counter() - q_t0) * 1000  # ms
            latencies.append(q_elapsed)

        total_query_time = time.perf_counter() - query_start
        query_qps = num_queries / total_query_time

        latencies.sort()
        p50 = latencies[int(len(latencies) * 0.50)]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]

        print(f"Query Benchmark: {num_queries} queries in {total_query_time:.3f} s ({query_qps:.1f} QPS)")
        print(f"Latencies: p50={p50:.2f}ms, p95={p95:.2f}ms, p99={p99:.2f}ms")

        assert query_qps > 200.0, f"Query QPS {query_qps:.1f} below SLA"
        assert p95 < 20.0, f"p95 latency {p95:.2f}ms exceeds 20ms threshold"

        # Cleanup test data
        session.execute(text(f"DELETE FROM event_state_snapshots WHERE event_id = '{test_event_id}'"))
        session.execute(text(f"DELETE FROM events WHERE id = '{test_event_id}'"))
        session.commit()

        print("✓ PostgreSQL & TimescaleDB load test passed successfully!")

    finally:
        session.close()


if __name__ == "__main__":
    test_postgres_load_and_hypertables()
