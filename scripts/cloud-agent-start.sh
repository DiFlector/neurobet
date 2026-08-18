#!/usr/bin/env bash
# Per-boot startup for the NeuroBet stack: reconcile the Postgres daemon and make
# sure the schema is present, then return. The application services (backend,
# ai_service, frontend) run as long-lived `terminals`, not here.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# shellcheck disable=SC1091
set -a; . "$REPO_ROOT/scripts/dev.env"; set +a

echo "==> Ensuring Postgres cluster is running"
sudo pg_ctlcluster 16 main start 2>/dev/null || true
for _ in $(seq 1 30); do
  if pg_isready -h localhost -p 5432 >/dev/null 2>&1; then break; fi
  sleep 1
done
pg_isready -h localhost -p 5432

# Idempotent safety net: applies any pending migrations (no-op when already current),
# so the schema is guaranteed present even on a fresh cluster.
if [ -x "$REPO_ROOT/.venv/bin/alembic" ]; then
  echo "==> Applying database migrations (if any)"
  ( cd db && DATABASE_URL="$DATABASE_URL" "$REPO_ROOT/.venv/bin/alembic" upgrade head )
fi

[ -f "$REPO_ROOT/.env" ] || cp "$REPO_ROOT/scripts/dev.env" "$REPO_ROOT/.env"

echo "==> Start complete; Postgres is ready on localhost:5432."
