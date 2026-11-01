"""Persist pair result evidence without projecting it into singles history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "062_doubles_match_obs"
down_revision = "061_tennis_doubles_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tennis_doubles_match_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(48), nullable=False),
        sa.Column("source_match_id", sa.String(128), nullable=False),
        sa.Column("source_profile_player_id", sa.String(64), nullable=False),
        sa.Column("observation_hash", sa.String(64), nullable=False),
        sa.Column("played_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discipline", sa.String(24), nullable=False, server_default="unknown_doubles"),
        sa.Column("source_url", sa.String(512), nullable=False),
        sa.Column("source_content_hash", sa.String(80)),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source", "source_match_id", "available_at", "observation_hash",
                            name="uq_tennis_doubles_match_observation_version"),
        sa.CheckConstraint("discipline IN ('mens_doubles','womens_doubles','mixed_doubles','unknown_doubles')",
                           name="ck_tennis_doubles_match_discipline"),
    )
    op.create_index("idx_tennis_doubles_match_observation_asof", "tennis_doubles_match_observations", ["source_match_id", "available_at"])
    op.create_index("idx_tennis_doubles_match_discipline_time", "tennis_doubles_match_observations", ["discipline", "played_at"])


def downgrade():
    op.drop_index("idx_tennis_doubles_match_discipline_time", table_name="tennis_doubles_match_observations")
    op.drop_index("idx_tennis_doubles_match_observation_asof", table_name="tennis_doubles_match_observations")
    op.drop_table("tennis_doubles_match_observations")
