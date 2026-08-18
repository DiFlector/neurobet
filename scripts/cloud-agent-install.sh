#!/usr/bin/env bash
# Idempotent Cloud Agent install for the NeuroBet stack, run natively (no Docker).
#
# Brings the VM to a fully prepared state: Postgres 16 installed with an `autobet`
# role + database and the Alembic schema applied, a Python venv with the backend and
# ai_service dependencies, and the frontend's pnpm dependencies. Safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# shellcheck disable=SC1091
set -a; . "$REPO_ROOT/scripts/dev.env"; set +a

echo "==> [1/7] System packages (Postgres, build tools)"
if ! command -v pg_ctlcluster >/dev/null 2>&1 || ! command -v psql >/dev/null 2>&1; then
  sudo apt-get update -y
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    postgresql postgresql-client libgomp1 python3-venv python3-dev build-essential
else
  echo "    Postgres already installed; skipping apt."
fi

echo "==> [2/7] Start Postgres cluster"
sudo pg_ctlcluster 16 main start 2>/dev/null || true
for _ in $(seq 1 30); do
  if pg_isready -h localhost -p 5432 >/dev/null 2>&1; then break; fi
  sleep 1
done
pg_isready -h localhost -p 5432

echo "==> [3/7] Ensure role + database exist"
sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
  "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='${POSTGRES_USER}') THEN CREATE ROLE ${POSTGRES_USER} LOGIN PASSWORD '${POSTGRES_PASSWORD}'; END IF; END \$\$;"
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}'" | grep -q 1; then
  sudo -u postgres createdb -O "${POSTGRES_USER}" "${POSTGRES_DB}"
fi

echo "==> [4/7] Generate root .env (read by backend/settings.py) if missing"
if [ ! -f "$REPO_ROOT/.env" ]; then
  cp "$REPO_ROOT/scripts/dev.env" "$REPO_ROOT/.env"
fi
mkdir -p "$REPO_ROOT/data/models"

echo "==> [5/7] Python venv + backend/ai_service dependencies"
if [ ! -x "$REPO_ROOT/.venv/bin/python" ]; then
  python3 -m venv "$REPO_ROOT/.venv"
fi
"$REPO_ROOT/.venv/bin/pip" install --upgrade pip
"$REPO_ROOT/.venv/bin/pip" install -r backend/requirements.txt -r ai_service/requirements.txt

echo "==> [6/7] Frontend dependencies (pnpm)"
( cd frontend/autobet && pnpm install --frozen-lockfile )

echo "==> [7/7] Apply database migrations (Alembic)"
( cd db && DATABASE_URL="$DATABASE_URL" "$REPO_ROOT/.venv/bin/alembic" upgrade head )

echo "==> Install complete."
