#!/usr/bin/env bash
set -e

echo "=== Syncing volleyball packages and contracts to containers ==="
docker cp packages/contracts/. neurobet_backend:/app/packages/contracts/
docker cp packages/contracts/. neurobet_bet_manager:/app/packages/contracts/
docker cp packages/contracts/. neurobet_neural:/app/packages/contracts/

docker cp packages/features/. neurobet_backend:/app/packages/features/
docker cp packages/features/. neurobet_neural:/app/packages/features/

docker cp packages/sports-core/. neurobet_backend:/app/packages/sports-core/
docker cp packages/sports-core/. neurobet_bet_manager:/app/packages/sports-core/
docker cp packages/sports-core/. neurobet_neural:/app/packages/sports-core/

docker exec neurobet_backend mkdir -p /app/sports/volleyball
docker exec neurobet_neural mkdir -p /app/sports/volleyball

docker cp sports/volleyball/. neurobet_backend:/app/sports/volleyball/
docker cp sports/volleyball/. neurobet_neural:/app/sports/volleyball/

docker cp services/neural/app/. neurobet_neural:/app/app/
docker exec neurobet_backend mkdir -p /app/services/neural
docker cp services/neural/. neurobet_backend:/app/services/neural/
docker cp services/backend/app/. neurobet_backend:/app/app/

echo "=== Installing packages in containers ==="
docker exec neurobet_backend pip install -e /app/packages/contracts
docker exec neurobet_backend pip install -e /app/packages/features
docker exec neurobet_backend pip install -e /app/packages/sports-core
docker exec neurobet_backend pip install -e /app/sports/volleyball

docker exec neurobet_neural pip install -e /app/packages/contracts
docker exec neurobet_neural pip install -e /app/packages/features
docker exec neurobet_neural pip install -e /app/packages/sports-core
docker exec neurobet_neural pip install -e /app/sports/volleyball

echo "=== Running Phase 27 Volleyball Test Suite ==="
docker exec -i neurobet_backend python - < tests/sports/test_volleyball_adapter.py
