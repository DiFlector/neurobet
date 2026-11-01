"""Store source URL and parser version on immutable tennis ranking observations.

Revision ID: 052_tennis_rank_source
Revises: 051_external_tennis_ingestion
"""

from alembic import op

revision = "052_tennis_rank_source"
down_revision = "051_external_tennis_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tennis_player_ranking_snapshots ADD COLUMN IF NOT EXISTS source_url VARCHAR(512)")
    op.execute("ALTER TABLE tennis_player_ranking_snapshots ADD COLUMN IF NOT EXISTS parser_version VARCHAR(64)")
    op.execute("""
      CREATE UNIQUE INDEX IF NOT EXISTS uq_tennis_ranking_observation_pit_key
      ON tennis_player_ranking_snapshots
        (source, source_player_id, tour, COALESCE(ranking_date, '1970-01-01 00:00:00+00'::timestamptz), available_at)
    """)


def downgrade() -> None:
    op.drop_index("uq_tennis_ranking_observation_pit_key", table_name="tennis_player_ranking_snapshots")
    op.drop_column("tennis_player_ranking_snapshots", "parser_version")
    op.drop_column("tennis_player_ranking_snapshots", "source_url")
