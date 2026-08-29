#!/usr/bin/env bash
# Импорт .nbarchive.zip без загрузки через браузер (обход nginx client_max_body_size).
#
#   cd /srv/neurobet
#   ./scripts/import_nbarchive.sh /path/to/neurobet-archive.nbarchive.zip
#
# Dev (по умолчанию при NEUROBET_DEPLOY_MODE=dev в scripts/dev.env):
#   ./scripts/import_nbarchive.sh --dev /path/to/archive.nbarchive.zip
#
# Prod backend:
#   ./scripts/import_nbarchive.sh --prod /path/to/archive.nbarchive.zip

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../" && pwd)"
cd "$ROOT"

MODE="prod"
ZIP=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev)
      MODE="dev"
      shift
      ;;
    --prod)
      MODE="prod"
      shift
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      ZIP="$1"
      shift
      ;;
  esac
done

if [[ -z "$ZIP" || ! -f "$ZIP" ]]; then
  echo "Usage: $0 [--dev|--prod] /path/to/*.nbarchive.zip" >&2
  exit 1
fi

if [[ "$MODE" == "dev" ]]; then
  CONTAINER="${NEUROBET_BACKEND_CONTAINER:-neurobet_dev_backend}"
else
  CONTAINER="${NEUROBET_BACKEND_CONTAINER:-neurobet_backend}"
fi

SIZE="$(stat -c%s "$ZIP" 2>/dev/null || stat -f%z "$ZIP")"
echo "Container: $CONTAINER"
echo "Archive: $ZIP ($(numfmt --to=iec "$SIZE" 2>/dev/null || echo "${SIZE} bytes"))"

docker cp "$ZIP" "$CONTAINER:/tmp/nbarchive-import.zip"

docker exec "$CONTAINER" python3 -c "
from archive_transfer import import_archive_zip_file

result = import_archive_zip_file('/tmp/nbarchive-import.zip')
counts = result.get('counts') or {}
print('Import OK')
print(f'  finished_events: {counts.get(\"finished_events\")}')
print(f'  finished_bets: {counts.get(\"finished_bets\")}')
print(f'  finished_odds_history: {counts.get(\"finished_odds_history\")}')
print(f'  team_stats: {result.get(\"team_stats_imported\")}')
"

docker exec "$CONTAINER" rm -f /tmp/nbarchive-import.zip

docker exec "$CONTAINER" python3 -c "
import os, httpx
ai = os.getenv('AI_SERVICE_URL', 'http://ai:8001')
try:
    with httpx.Client(timeout=120.0) as client:
        client.post(f'{ai}/internal/reload-team-stats', json={'force_db': True})
    print('Team stats cache reloaded')
except Exception as e:
    print('Team stats reload skipped:', e)
"

echo "Done."
