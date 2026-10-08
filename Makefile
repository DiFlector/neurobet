.PHONY: up down infra-up infra-down logs ps test lint migrate train clean

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

# Run contract test suite
test:
	docker exec -i neurobet_backend python - < tests/contract/test_contracts.py

# Run linter
lint:
	ruff check . || true

# Run database migrations
migrate:
	docker exec -it neurobet_backend alembic upgrade head

# Run ML model training for tennis
train:
	docker exec -it neurobet_neural python -m app.train --sport tennis

# Clean build artifacts and pycache
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
