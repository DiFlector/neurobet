#!/usr/bin/env bash
set -e

echo "=== Syncing contracts and packages to containers ==="
docker cp /srv/neurobet/packages/contracts/. neurobet_backend:/app/packages/contracts/
docker cp /srv/neurobet/packages/contracts/. neurobet_bet_manager:/app/packages/contracts/
docker cp /srv/neurobet/packages/contracts/. neurobet_neural:/app/packages/contracts/

docker cp /srv/neurobet/packages/db/. neurobet_backend:/app/packages/db/
docker cp /srv/neurobet/packages/db/. neurobet_bet_manager:/app/packages/db/
docker cp /srv/neurobet/packages/db/. neurobet_neural:/app/packages/db/

docker cp /srv/neurobet/packages/bankroll/. neurobet_backend:/app/packages/bankroll/
docker cp /srv/neurobet/packages/bankroll/. neurobet_bet_manager:/app/packages/bankroll/

docker cp /srv/neurobet/services/backend/app/. neurobet_backend:/app/app/
docker cp /srv/neurobet/services/bet-manager/app/. neurobet_bet_manager:/app/app/
docker cp /srv/neurobet/services/neural/app/. neurobet_neural:/app/app/

echo "=== Installing contracts in containers ==="
docker exec neurobet_backend pip install -e /app/packages/contracts --no-deps
docker exec neurobet_bet_manager pip install -e /app/packages/contracts --no-deps
docker exec neurobet_neural pip install -e /app/packages/contracts --no-deps

echo "=== Running Phase 25 Shadow Mode Test Suite ==="
docker exec -i neurobet_backend python - < /srv/neurobet/tests/neural/test_shadow_mode.py

echo "=== Phase 25 Sync & Test Suite Completed Successfully ==="
