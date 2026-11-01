FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY packages/contracts /app/packages/contracts
RUN pip install --no-cache-dir /app/packages/contracts

COPY packages/observability /app/packages/observability
RUN pip install --no-cache-dir /app/packages/observability

COPY packages/db /app/packages/db
RUN pip install --no-cache-dir /app/packages/db

COPY sports/tennis /app/packages/sport-tennis
RUN pip install --no-cache-dir /app/packages/sport-tennis

COPY services/research/pyproject.toml /app/
RUN pip install --no-cache-dir "playwright==1.64.1"

COPY packages/sports-core /app/packages/sports-core
RUN pip install --no-cache-dir /app/packages/sports-core

COPY services/research/app /app/app
COPY scripts/import_api_tennis_archive.py /app/scripts/import_api_tennis_archive.py
COPY scripts/import_api_tennis_pbp_archive.py /app/scripts/import_api_tennis_pbp_archive.py
COPY scripts/import_api_tennis_canonical_archive.py /app/scripts/import_api_tennis_canonical_archive.py
COPY scripts/research/materialize_api_tennis_serve_logs.py /app/scripts/materialize_api_tennis_serve_logs.py
COPY scripts/research/adjudicate_tennis_player_alias.py /app/scripts/adjudicate_tennis_player_alias.py
COPY scripts/research/reconcile_api_tennis_player_alias_conflicts.py /app/scripts/reconcile_api_tennis_player_alias_conflicts.py
COPY scripts/research/recover_tennis_result_witnesses.py /app/scripts/recover_tennis_result_witnesses.py
COPY scripts/research/prepare_tennis_recovered_cohort.py /app/scripts/prepare_tennis_recovered_cohort.py
COPY scripts/research/reproject_canonical_tennis_matches.py /app/scripts/reproject_canonical_tennis_matches.py
COPY scripts/research/reassign_tennis_player_alias.py /app/scripts/reassign_tennis_player_alias.py
COPY scripts/research/tennis_result_observation.py /app/scripts/tennis_result_observation.py
COPY scripts/research/tennis_label_audit.py /app/scripts/tennis_label_audit.py
COPY scripts/research/run_tennis_label_audit.py /app/scripts/run_tennis_label_audit.py
COPY scripts/research/run_tennis_full_census.py /app/scripts/run_tennis_full_census.py
COPY scripts/research/tennis_snapshot_cache.py /app/scripts/tennis_snapshot_cache.py
COPY scripts/research/tennis_event_archive.py /app/scripts/tennis_event_archive.py
COPY scripts/research/tennis_corpus_counts.py /app/scripts/tennis_corpus_counts.py
COPY scripts/research/continue_tennis_event_archive.py /app/scripts/continue_tennis_event_archive.py
COPY scripts/research/tennis_label_audit_resumable.py /app/scripts/tennis_label_audit_resumable.py
COPY scripts/research/tennis_history_coverage.py /app/scripts/tennis_history_coverage.py
COPY scripts/research/repair_tennis_point_link_orientation.py /app/scripts/repair_tennis_point_link_orientation.py
COPY scripts/research/link_api_tennis_histories.py /app/scripts/link_api_tennis_histories.py
COPY scripts/research/backfill_tennis_snapshot_raw_evidence.py /app/scripts/backfill_tennis_snapshot_raw_evidence.py
COPY services/research/app/tennis_pit_raw_evidence.py /app/app/tennis_pit_raw_evidence.py
COPY scripts/research/replay_tennis_catalog_evidence.py /app/scripts/replay_tennis_catalog_evidence.py
RUN pip install --no-cache-dir .

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD test -f /tmp/healthy || exit 1

CMD ["python", "-m", "app.main"]
