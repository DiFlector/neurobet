#!/usr/bin/env bash
# Per-boot startup for the NeuroBet stack. Brings up Postgres, applies migrations,
# and launches the three application services (backend, ai_service, frontend) as
# detached background processes, then returns once they are ready.
#
# Idempotent: re-running it will not double-start a service that is already
# listening on its port. Logs are written under data/logs/.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# shellcheck disable=SC1091
set -a; . "$REPO_ROOT/scripts/dev.env"; set +a

LOG_DIR="$REPO_ROOT/data/logs"
mkdir -p "$LOG_DIR" "$REPO_ROOT/data/models"
[ -f "$REPO_ROOT/.env" ] || cp "$REPO_ROOT/scripts/dev.env" "$REPO_ROOT/.env"

port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && { exec 3>&- 3<&-; return 0; } || return 1; }

wait_for_port() {
  local port="$1" name="$2" tries="${3:-60}"
  for _ in $(seq 1 "$tries"); do
    if port_in_use "$port"; then echo "    $name is up on :$port"; return 0; fi
    sleep 1
  done
  echo "    WARNING: $name did not open :$port in time (see $LOG_DIR)"; return 1
}

launch() {
  # launch <name> <port> <command...>
  local name="$1" port="$2"; shift 2
  if port_in_use "$port"; then
    echo "==> $name already running on :$port; skipping."
    return 0
  fi
  echo "==> Starting $name on :$port"
  setsid bash -c "$*" >"$LOG_DIR/$name.log" 2>&1 < /dev/null &
}

echo "==> Ensuring Postgres cluster is running"
sudo pg_ctlcluster 16 main start 2>/dev/null || true
for _ in $(seq 1 30); do pg_isready -h localhost -p 5432 >/dev/null 2>&1 && break; sleep 1; done
pg_isready -h localhost -p 5432

if [ -x "$REPO_ROOT/.venv/bin/alembic" ]; then
  echo "==> Applying database migrations (if any)"
  ( cd db && DATABASE_URL="$DATABASE_URL" "$REPO_ROOT/.venv/bin/alembic" upgrade head )
fi

launch backend 8000 \
  "cd '$REPO_ROOT' && set -a && . scripts/dev.env && set +a && . .venv/bin/activate && cd backend && exec uvicorn main:app --host 0.0.0.0 --port 8000"

launch ai_service 8001 \
  "cd '$REPO_ROOT' && set -a && . scripts/dev.env && set +a && export MODEL_DIR='$REPO_ROOT/data/models' && . .venv/bin/activate && cd ai_service && exec uvicorn main:app --host 0.0.0.0 --port 8001"

launch frontend 3000 \
  "cd '$REPO_ROOT' && set -a && . scripts/dev.env && set +a && cd frontend/autobet && exec pnpm dev"

wait_for_port 8001 ai_service 60 || true
wait_for_port 8000 backend 60 || true
wait_for_port 3000 frontend 90 || true

echo "==> Start complete."
echo "    Frontend:   http://localhost:3000/neurobet"
echo "    Backend API: http://localhost:8000/api/stats"
echo "    AI service:  http://localhost:8001/settings"
