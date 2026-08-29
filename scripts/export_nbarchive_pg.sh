#!/usr/bin/env bash
# Собрать .nbarchive.zip из Postgres (архив finished + team_stats.json).
#
#   cd /srv/neurobet
#   ./scripts/export_nbarchive_pg.sh
#
# Файл появится в каталоге neurobet: neurobet-archive-YYYYMMDD-HHMMSS.nbarchive.zip
# Опционально: ./scripts/export_nbarchive_pg.sh /path/to/custom.nbarchive.zip

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../" && pwd)"
cd "$ROOT"

CONTAINER="${NEUROBET_PG_CONTAINER:-neurobet_postgres}"
DB="${POSTGRES_DB:-autobet}"
USER="${POSTGRES_USER:-autobet}"
TEAM_STATS="${TEAM_STATS_PATH:-$ROOT/data/models/team_stats.json}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${1:-$ROOT/neurobet-archive-${STAMP}.nbarchive.zip}"
# /tmp is tmpfs (~half of RAM) with usrquota. A SQL dump of finished_bets
# (multi-GB) hits EDQUOT: "write /dev/stdout: disk quota exceeded".
# Stage on the project disk and dump inside the bind-mounted PGDATA.
STAGING_ROOT="$ROOT/data"
mkdir -p "$STAGING_ROOT"
TMP="$(mktemp -d -p "$STAGING_ROOT" .nbarchive-XXXXXX)"
DUMP_IN_PG="/var/lib/postgresql/data/.nbarchive_finished_data.sql"

PROGRESS_PID=""

human_bytes() {
  local b="${1:-0}"
  if (( b >= 1073741824 )); then
    printf '%.1fG' "$(awk "BEGIN {printf \"%.1f\", $b/1073741824}")"
  elif (( b >= 1048576 )); then
    printf '%.1fM' "$(awk "BEGIN {printf \"%.1f\", $b/1048576}")"
  elif (( b >= 1024 )); then
    printf '%.1fK' "$(awk "BEGIN {printf \"%.1f\", $b/1024}")"
  else
    printf '%dB' "$b"
  fi
}

progress_line() {
  local phase="$1"
  local pct="$2"
  local cur="$3"
  local total="$4"
  local msg
  msg="$(printf '[%-9s] %3d%%  %s / ~%s' "$phase" "$pct" "$(human_bytes "$cur")" "$(human_bytes "$total")")"
  if [[ -t 1 ]]; then
    printf '\r%-60s' "$msg"
  else
    printf '%s\n' "$msg"
  fi
}

progress_done() {
  local phase="$1"
  local cur="$2"
  if [[ -t 1 ]]; then
    printf '\r[%-9s] 100%%  %s                    \n' "$phase" "$(human_bytes "$cur")"
  else
    printf '[%-9s] 100%%  %s\n' "$phase" "$(human_bytes "$cur")"
  fi
}

file_size_in_container() {
  docker exec "$CONTAINER" stat -c%s "$1" 2>/dev/null || echo 0
}

file_size_local() {
  if [[ -f "$1" ]]; then
    stat -c%s "$1"
  else
    echo 0
  fi
}

stop_progress() {
  if [[ -n "$PROGRESS_PID" ]]; then
    kill "$PROGRESS_PID" 2>/dev/null || true
    wait "$PROGRESS_PID" 2>/dev/null || true
    PROGRESS_PID=""
  fi
}

watch_file_progress() {
  local phase="$1"
  local estimate="$2"
  local pid="$3"
  local mode="$4"
  local path="$5"
  local target="$estimate"

  while kill -0 "$pid" 2>/dev/null; do
    local cur=0
    if [[ "$mode" == container ]]; then
      cur="$(file_size_in_container "$path")"
    else
      cur="$(file_size_local "$path")"
    fi
    # pg_dump often exceeds pg_relation_size; grow the bar instead of sticking at 99%.
    if (( cur > target * 90 / 100 )); then
      target=$(( cur * 110 / 100 ))
    fi
    (( target < estimate )) && target="$estimate"
    local pct=0
    if (( target > 0 )); then
      pct=$(( cur * 100 / target ))
      (( pct > 99 )) && pct=99
    fi
    progress_line "$phase" "$pct" "$cur" "$target"
    sleep 1
  done
}

cleanup() {
  stop_progress
  rm -rf "$TMP"
  docker exec "$CONTAINER" rm -f "$DUMP_IN_PG" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Postgres container: $CONTAINER, database: $DB"

STATS="$(
  docker exec -i "$CONTAINER" psql -U "$USER" -d "$DB" -t -A -F' ' <<'SQL'
SELECT
  (SELECT COUNT(*) FROM finished.finished_events),
  (SELECT COUNT(*) FROM finished.finished_bets),
  (SELECT COUNT(*) FROM finished.finished_odds_history),
  (
    pg_relation_size('finished.finished_events') +
    pg_relation_size('finished.finished_bets') +
    pg_relation_size('finished.finished_odds_history')
  ) * 12 / 10;
SQL
)"
read -r FE FB FO ESTIMATE_BYTES <<< "$STATS"

echo "  finished_events: $FE"
echo "  finished_bets: $FB"
echo "  finished_odds_history: $FO"
echo "  estimated dump: $(human_bytes "$ESTIMATE_BYTES")"
echo

# --- pg_dump ---
docker exec "$CONTAINER" pg_dump -U "$USER" -d "$DB" \
  --schema=finished --data-only --no-owner --no-privileges \
  -t 'finished.finished_events' \
  -t 'finished.finished_bets' \
  -t 'finished.finished_odds_history' \
  -f "$DUMP_IN_PG" &
DUMP_PID=$!
watch_file_progress "pg_dump" "$ESTIMATE_BYTES" "$DUMP_PID" container "$DUMP_IN_PG" &
PROGRESS_PID=$!
if ! wait "$DUMP_PID"; then
  echo "pg_dump failed (exit $?)" >&2
  exit 1
fi
stop_progress
DUMP_BYTES="$(file_size_in_container "$DUMP_IN_PG")"
progress_done "pg_dump" "$DUMP_BYTES"

# --- docker cp ---
docker cp "$CONTAINER:$DUMP_IN_PG" "$TMP/finished_data.sql" &
CP_PID=$!
watch_file_progress "docker cp" "$DUMP_BYTES" "$CP_PID" local "$TMP/finished_data.sql" &
PROGRESS_PID=$!
wait "$CP_PID"
stop_progress
progress_done "docker cp" "$DUMP_BYTES"
docker exec "$CONTAINER" rm -f "$DUMP_IN_PG"

HAS_TS=false
if [[ -f "$TEAM_STATS" ]]; then
  HAS_TS=true
fi

EXPORTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "$TMP/manifest.json" <<EOF
{
  "format_version": 1,
  "exported_at": "$EXPORTED_AT",
  "source": "postgres-export",
  "counts": {
    "finished_events": $FE,
    "finished_bets": $FB,
    "finished_odds_history": $FO
  },
  "has_team_stats": $HAS_TS
}
EOF

if [[ "$HAS_TS" == true ]]; then
  cp "$TEAM_STATS" "$TMP/team_stats.json"
fi

ZIP_INPUT_BYTES="$DUMP_BYTES"
if [[ -f "$TMP/manifest.json" ]]; then
  ZIP_INPUT_BYTES=$(( ZIP_INPUT_BYTES + $(file_size_local "$TMP/manifest.json") ))
fi
if [[ "$HAS_TS" == true ]]; then
  ZIP_INPUT_BYTES=$(( ZIP_INPUT_BYTES + $(file_size_local "$TMP/team_stats.json") ))
fi
# SQL compresses well; finished_data.sql dominates the archive.
ZIP_ESTIMATE=$(( ZIP_INPUT_BYTES / 3 ))
(( ZIP_ESTIMATE < 1048576 )) && ZIP_ESTIMATE=1048576

python3 - "$OUT" "$TMP" "$HAS_TS" <<'PY' &
import sys, zipfile
from pathlib import Path

out, tmp, has_ts = sys.argv[1], Path(sys.argv[2]), sys.argv[3] == "true"
names = ["manifest.json", "finished_data.sql"]
if has_ts:
    names.append("team_stats.json")
with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for name in names:
        zf.write(tmp / name, name)
PY
ZIP_PID=$!
watch_file_progress "zip" "$ZIP_ESTIMATE" "$ZIP_PID" local "$OUT" &
PROGRESS_PID=$!
wait "$ZIP_PID"
stop_progress
ZIP_BYTES="$(file_size_local "$OUT")"
progress_done "zip" "$ZIP_BYTES"

echo
echo "Created: $OUT"
echo "  finished_events: $FE"
echo "  finished_bets: $FB"
echo "  finished_odds_history: $FO"
echo "  team_stats.json: $HAS_TS"
echo "  archive size: $(human_bytes "$ZIP_BYTES")"
