"""Immutable PIT observations for tennis event format and surface."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "056_tennis_event_attrs"
down_revision = "055_flashscore_recovery"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tennis_event_attribute_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attribute_name", sa.String(length=24), nullable=False),
        sa.Column("value_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.String(length=48), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("identity_available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_object_key", sa.String(length=1024), nullable=True),
        sa.Column("source_content_hash", sa.String(length=80), nullable=True),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("reconstructed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", "attribute_name", "source", "available_at", "evidence_hash",
                            name="uq_tennis_event_attribute_observation_evidence"),
        sa.CheckConstraint("attribute_name IN ('best_of','surface')", name="ck_tennis_event_attribute_name"),
    )
    op.create_index(
        "idx_tennis_event_attribute_asof",
        "tennis_event_attribute_observations",
        ["event_id", "attribute_name", "available_at", "id"],
    )


def downgrade():
    op.drop_index("idx_tennis_event_attribute_asof", table_name="tennis_event_attribute_observations")
    op.drop_table("tennis_event_attribute_observations")
