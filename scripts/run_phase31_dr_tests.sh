#!/usr/bin/env bash
set -euo pipefail

echo "============================================================"
echo "🛡️  NEUROBET PHASE 31: DISASTER RECOVERY VALIDATION SUITE"
echo "============================================================"
echo "Timestamp: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo ""

START_TIME=$(date +%s)

echo "[1/4] Running PostgreSQL Database Restore & Parity Test..."
PYTHONPATH=/srv/neurobet python3 /srv/neurobet/tests/disaster_recovery/test_db_restore.py
echo ""

echo "[2/4] Running Complete Docker Compose Stack Restart & Recovery Test..."
python3 /srv/neurobet/tests/disaster_recovery/test_stack_restart.py
echo ""

echo "[3/4] Running Immutable Ledger Balance Reconstruction Test..."
docker exec -i neurobet_backend python - < /srv/neurobet/tests/disaster_recovery/test_balance_reconstruction.py
echo ""

echo "[4/4] Running Bet Decision Audit Provenance Reconstruction Test..."
docker exec -i neurobet_backend python - < /srv/neurobet/tests/disaster_recovery/test_bet_decision_reconstruction.py
echo ""

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo "============================================================"
echo "🎉 ALL PHASE 31 DISASTER RECOVERY TESTS PASSED! (${DURATION}s)"
echo "============================================================"
