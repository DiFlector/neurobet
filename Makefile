.PHONY: up down infra-up infra-down logs ps test test-api test-frontend lint migrate train clean

# Environment variables
COMPOSE = docker compose
ENV_FILE = .env

# Start all containers
up:
	$(COMPOSE) --env-file $(ENV_FILE) up -d --build

# Stop all containers
down:
	$(COMPOSE) --env-file $(ENV_FILE) down

# Start only infrastructure containers (PostgreSQL + Timescale, Redis, MinIO)
infra-up:
	$(COMPOSE) --env-file $(ENV_FILE) up -d postgres redis minio

# Stop infrastructure containers
infra-down:
	$(COMPOSE) --env-file $(ENV_FILE) stop postgres redis minio

# View logs
logs:
	$(COMPOSE) --env-file $(ENV_FILE) logs -f

# Check container status
ps:
	$(COMPOSE) --env-file $(ENV_FILE) ps

# Run test suite
test:
	docker exec -i neurobet_backend python - < tests/contract/test_contracts.py
	docker exec -i neurobet_backend python - < tests/streams/test_streams.py
	docker exec -i neurobet_collector python - < tests/collector/test_collector.py
	docker exec -i neurobet_backend python - < tests/sports/test_tennis_adapter.py
	docker exec -i neurobet_backend python - < tests/sports/test_generic_layer.py
	docker exec -i neurobet_backend python - < tests/quality/test_data_quality.py
	docker exec -i neurobet_backend python - < tests/features/test_feature_engineering.py
	docker exec -i neurobet_neural python - < tests/neural/test_baseline_ml.py
	docker exec -i neurobet_neural python - < tests/neural/test_backtester.py
	docker exec -i neurobet_backend python - < tests/bankroll/test_ledger_audit.py
	docker exec -i neurobet_bet_manager python - < tests/bet_manager/test_bet_manager.py
	docker exec -i neurobet_backend python - < tests/settlement/test_settlement.py
	docker exec -i neurobet_llm python - < tests/llm/test_llm.py
	docker exec -i neurobet_research python - < tests/research/test_research.py
	docker exec -i neurobet_backend python - < tests/decision_layer/test_decision_layer.py
	docker exec -i neurobet_backend python - < tests/scheduler/test_candidate_scheduler.py
	docker exec -i neurobet_backend python - < tests/api/test_backend_api.py
	docker exec -i neurobet_backend python - < tests/frontend/test_frontend.py
	docker exec -i neurobet_backend python - < tests/observability/test_observability.py
	docker exec -i neurobet_backend python - < tests/leakage/test_data_leakage.py

	python3 tests/security/test_security_hardening.py
	docker exec -i neurobet_neural python - < tests/experiments/test_baseline_experiment.py


test-experiment:
	docker exec -i neurobet_neural python - < tests/experiments/test_baseline_experiment.py

test-leakage:
	docker exec -i neurobet_backend python - < tests/leakage/test_data_leakage.py

test-security:
	python3 tests/security/test_security_hardening.py



test-observability:
	docker exec -i neurobet_backend python - < tests/observability/test_observability.py

test-frontend:
	docker exec -i neurobet_backend python - < tests/frontend/test_frontend.py

test-api:
	docker exec -i neurobet_backend python - < tests/api/test_backend_api.py

test-contracts:
	docker exec -i neurobet_backend python - < tests/contract/test_contracts.py

test-streams:
	docker exec -i neurobet_backend python - < tests/streams/test_streams.py

test-collector:
	docker exec -i neurobet_collector python - < tests/collector/test_collector.py

test-sports:
	docker exec -i neurobet_backend python - < tests/sports/test_tennis_adapter.py

test-generic:
	docker exec -i neurobet_backend python - < tests/sports/test_generic_layer.py

test-quality:
	docker exec -i neurobet_backend python - < tests/quality/test_data_quality.py

test-features:
	docker exec -i neurobet_backend python - < tests/features/test_feature_engineering.py

test-neural:
	docker exec -i neurobet_neural python - < tests/neural/test_baseline_ml.py

test-backtest:
	docker exec -i neurobet_neural python - < tests/neural/test_backtester.py

test-bankroll:
	docker exec -i neurobet_backend python - < tests/bankroll/test_ledger_audit.py

test-bet-manager:
	docker exec -i neurobet_bet_manager python - < tests/bet_manager/test_bet_manager.py

test-settlement:
	docker exec -i neurobet_backend python - < tests/settlement/test_settlement.py

test-llm:
	docker exec -i neurobet_llm python - < tests/llm/test_llm.py

test-research:
	docker exec -i neurobet_research python - < tests/research/test_research.py

test-decision-layer:
	docker exec -i neurobet_backend python - < tests/decision_layer/test_decision_layer.py

test-scheduler:
	docker exec -i neurobet_backend python - < tests/scheduler/test_candidate_scheduler.py

# Initialize Redis Streams and consumer groups
init-streams:
	docker exec -i neurobet_backend python - < scripts/init_streams.py

# Run linter
lint:
	ruff check . || true

# Run database migrations
migrate:
	docker exec -it neurobet_backend alembic upgrade head

# Run ML model training for tennis
train:
	docker exec -i neurobet_neural python -m app.train --sport tennis

# Run walk-forward backtest
backtest:
	docker exec -i neurobet_neural python -m app.backtest --sport tennis --strategy KELLY

# Clean build artifacts and pycache
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
