#!/usr/bin/env bash
set -e

echo "=== Syncing DB package and Performance Benchmark suites to containers ==="
docker cp packages/db/. neurobet_backend:/app/packages/db/
docker cp packages/db/. neurobet_neural:/app/packages/db/
docker exec neurobet_backend pip install -e /app/packages/db > /dev/null 2>&1 || true

docker exec neurobet_backend mkdir -p /app/tests/performance
docker cp tests/performance/. neurobet_backend:/app/tests/performance/

echo "=== 1/5 Running Collector High-Throughput Load Test ==="
docker exec -i neurobet_backend python - < tests/performance/test_collector_load.py

echo "=== 2/5 Running PostgreSQL & TimescaleDB Hypertables Load Test ==="
docker exec -i neurobet_backend python - < tests/performance/test_postgres_load.py

echo "=== 3/5 Running Redis Streams High-Throughput Load Test ==="
docker exec -i neurobet_backend python - < tests/performance/test_redis_load.py

echo "=== 4/5 Running Latency Benchmarks (Inference, LLM, Research) ==="
docker exec -i neurobet_backend python - < tests/performance/test_latency_benchmarks.py

echo "=== 5/5 Running Scale Projections & Storage Lifecycle Verification ==="
docker exec -i neurobet_backend python - < tests/performance/test_scale_projections.py

echo ""
echo "🎉 ALL PHASE 30 PERFORMANCE & SCALE BENCHMARKS COMPLETED SUCCESSFULLY!"
