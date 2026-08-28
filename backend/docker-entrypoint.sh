#!/bin/sh
set -e

if [ -n "${DATABASE_URL:-}" ] && [ -d /db/migrations ]; then
  echo "Applying Alembic migrations (if any pending)..."
  cd /db
  alembic upgrade head
  cd /app
fi

exec "$@"
