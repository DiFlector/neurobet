#!/usr/bin/env bash
set -euo pipefail

echo "============================================================"
echo "🛡️  NEUROBET PHASE 32: PRODUCTION-LIKE SIMULATION GATE"
echo "============================================================"
echo "Timestamp: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo ""

START_TIME=$(date +%s)

# Ensure container has latest scripts and docs
docker cp /srv/neurobet/scripts neurobet_backend:/app/scripts >/dev/null 2>&1 || true
docker cp /srv/neurobet/docs neurobet_backend:/app/docs >/dev/null 2>&1 || true

echo "[1/2] Running 500-Cycle Long-Running Simulation Stress Test..."
docker exec -i neurobet_backend python - < /srv/neurobet/tests/simulation_gate/test_long_running_simulation.py
echo ""

echo "[2/2] Running 17-Point Production Simulation Gate Suite..."
docker exec -i neurobet_backend python - < /srv/neurobet/tests/simulation_gate/test_production_simulation_gate.py
echo ""

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo "============================================================"
echo "🏆 ALL PHASE 32 SIMULATION GATE TESTS PASSED! (${DURATION}s)"
echo "============================================================"
