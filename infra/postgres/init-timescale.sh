#!/bin/bash
set -e

# Initialize PostgreSQL with TimescaleDB and UTC timezone
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
    ALTER DATABASE "$POSTGRES_DB" SET timezone TO 'UTC';
    SELECT version(), default_version FROM pg_available_extensions WHERE name = 'timescaledb';
EOSQL

echo "TimescaleDB initialized successfully with UTC timezone."
