"""Retain explicit ranked/unranked status in official ranking observations.

Revision ID: 053_tennis_rank_status
Revises: 052_tennis_rank_source
"""

from alembic import op

revision = "053_tennis_rank_status"
down_revision = "052_tennis_rank_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tennis_player_ranking_snapshots ADD COLUMN IF NOT EXISTS ranking_status VARCHAR(16)")
    op.execute("ALTER TABLE tennis_player_ranking_snapshots ALTER COLUMN rank DROP NOT NULL")


def downgrade() -> None:
    op.execute("DELETE FROM tennis_player_ranking_snapshots WHERE rank IS NULL")
    op.execute("ALTER TABLE tennis_player_ranking_snapshots ALTER COLUMN rank SET NOT NULL")
    op.drop_column("tennis_player_ranking_snapshots", "ranking_status")
