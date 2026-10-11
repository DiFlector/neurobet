"""Performance indexes and TimescaleDB compression policies

Revision ID: 004_performance_and_compression
Revises: 003_immutable_ledger
Create Date: 2026-10-11 20:25:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "004_performance_and_compression"
down_revision: Union[str, None] = "003_immutable_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Performance Composite Indexes
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_events_sport_live_scheduled
    ON events (sport_code, is_live, scheduled_start_at DESC);
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_events_live_status
    ON events (is_live, status);
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_event_state_snapshots_event_time_desc
    ON event_state_snapshots (event_id, observed_at DESC);
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_odds_snapshots_event_time_desc
    ON odds_snapshots (event_id, observed_at DESC);
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_feature_snapshots_event_time_desc
    ON feature_snapshots (event_id, observed_at DESC);
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_bets_account_status
    ON bets (account_id, status);
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_bets_event_status
    ON bets (event_id, status);
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_ledger_entries_account_created_desc
    ON ledger_entries (account_id, created_at DESC);
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_ml_predictions_event_created_desc
    ON ml_predictions (event_id, created_at DESC);
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_raw_snapshots_sport_time
    ON raw_snapshots (sport_code, collected_at DESC);
    """)

    # 2. TimescaleDB Native Compression on Hypertables
    op.execute("""
    DO $$
    BEGIN
        -- Enable compression on event_state_snapshots
        IF EXISTS (
            SELECT 1 FROM timescaledb_information.hypertables
            WHERE hypertable_name = 'event_state_snapshots' AND compression_enabled = false
        ) THEN
            ALTER TABLE event_state_snapshots SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'sport_code, event_id',
                timescaledb.compress_orderby = 'observed_at DESC'
            );
            PERFORM add_compression_policy('event_state_snapshots', INTERVAL '7 days', if_not_exists => TRUE);
        END IF;

        -- Enable compression on odds_snapshots
        IF EXISTS (
            SELECT 1 FROM timescaledb_information.hypertables
            WHERE hypertable_name = 'odds_snapshots' AND compression_enabled = false
        ) THEN
            ALTER TABLE odds_snapshots SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'event_id, market_type',
                timescaledb.compress_orderby = 'observed_at DESC'
            );
            PERFORM add_compression_policy('odds_snapshots', INTERVAL '7 days', if_not_exists => TRUE);
        END IF;

        -- Enable compression on feature_snapshots
        IF EXISTS (
            SELECT 1 FROM timescaledb_information.hypertables
            WHERE hypertable_name = 'feature_snapshots' AND compression_enabled = false
        ) THEN
            ALTER TABLE feature_snapshots SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'sport_code, event_id',
                timescaledb.compress_orderby = 'observed_at DESC'
            );
            PERFORM add_compression_policy('feature_snapshots', INTERVAL '7 days', if_not_exists => TRUE);
        END IF;
    END $$;
    """)


def downgrade() -> None:
    # 1. Remove TimescaleDB compression policies
    op.execute("""
    DO $$
    BEGIN
        PERFORM remove_compression_policy('event_state_snapshots', if_exists => TRUE);
        PERFORM remove_compression_policy('odds_snapshots', if_exists => TRUE);
        PERFORM remove_compression_policy('feature_snapshots', if_exists => TRUE);
    END $$;
    """)

    # 2. Drop composite indexes
    op.execute("DROP INDEX IF EXISTS idx_events_sport_live_scheduled;")
    op.execute("DROP INDEX IF EXISTS idx_events_live_status;")
    op.execute("DROP INDEX IF EXISTS idx_event_state_snapshots_event_time_desc;")
    op.execute("DROP INDEX IF EXISTS idx_odds_snapshots_event_time_desc;")
    op.execute("DROP INDEX IF EXISTS idx_feature_snapshots_event_time_desc;")
    op.execute("DROP INDEX IF EXISTS idx_bets_account_status;")
    op.execute("DROP INDEX IF EXISTS idx_bets_event_status;")
    op.execute("DROP INDEX IF EXISTS idx_ledger_entries_account_created_desc;")
    op.execute("DROP INDEX IF EXISTS idx_ml_predictions_event_created_desc;")
    op.execute("DROP INDEX IF EXISTS idx_raw_snapshots_sport_time;")
