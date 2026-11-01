"""Add immutable tennis doubles roster evidence and canonical pair identities."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "061_tennis_doubles_foundation"
down_revision = "060_point_history_source_idx"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tennis_doubles_pairs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("player_low_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ext_tennis_canonical_players.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("player_high_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ext_tennis_canonical_players.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("player_low_id", "player_high_id", name="uq_tennis_doubles_pair_players"),
        sa.CheckConstraint("player_low_id < player_high_id", name="ck_tennis_doubles_pair_sorted"),
    )
    op.create_table(
        "tennis_doubles_roster_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("discipline", sa.String(24), nullable=False, server_default="unknown_doubles"),
        sa.Column("roster_json", postgresql.JSONB(), nullable=False),
        sa.Column("scoring_rules_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("confirmation_status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("source", sa.String(48), nullable=False),
        sa.Column("source_record_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_object_key", sa.String(1024)),
        sa.Column("raw_content_hash", sa.String(80)),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("event_id", "available_at", "evidence_hash", name="uq_tennis_doubles_roster_evidence"),
        sa.CheckConstraint("discipline IN ('mens_doubles','womens_doubles','mixed_doubles','unknown_doubles')", name="ck_tennis_doubles_roster_discipline"),
    )
    op.create_index("idx_tennis_doubles_roster_asof", "tennis_doubles_roster_observations", ["event_id", "available_at", "id"])
    op.create_index("idx_tennis_doubles_roster_status", "tennis_doubles_roster_observations", ["confirmation_status", "available_at"])


def downgrade():
    op.drop_index("idx_tennis_doubles_roster_status", table_name="tennis_doubles_roster_observations")
    op.drop_index("idx_tennis_doubles_roster_asof", table_name="tennis_doubles_roster_observations")
    op.drop_table("tennis_doubles_roster_observations")
    op.drop_table("tennis_doubles_pairs")
