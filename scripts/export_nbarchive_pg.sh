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
TMP="$(mktemp -d)"

cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

echo "Postgres container: $CONTAINER, database: $DB"

docker exec "$CONTAINER" pg_dump -U "$USER" -d "$DB" \
  --schema=finished --data-only --no-owner --no-privileges \
  -t finished.finished_events \
  -t finished.finished_bets \
  -t finished.finished_odds_history \
  > "$TMP/finished_data.sql"

FE=$(docker exec "$CONTAINER" psql -U "$USER" -d "$DB" -t -A -c "SELECT COUNT(*) FROM finished.finished_events;")
FB=$(docker exec "$CONTAINER" psql -U "$USER" -d "$DB" -t -A -c "SELECT COUNT(*) FROM finished.finished_bets;")
FO=$(docker exec "$CONTAINER" psql -U "$USER" -d "$DB" -t -A -c "SELECT COUNT(*) FROM finished.finished_odds_history;")

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

(
  cd "$TMP"
  if [[ "$HAS_TS" == true ]]; then
    zip -q "$OUT" manifest.json finished_data.sql team_stats.json
  else
    zip -q "$OUT" manifest.json finished_data.sql
  fi
)

echo "Created: $OUT"
echo "  finished_events: $FE"
echo "  finished_bets: $FB"
echo "  finished_odds_history: $FO"
echo "  team_stats.json: $HAS_TS"
