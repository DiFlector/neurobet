"""Append verified raw-object evidence for immutable tennis state slices.

Revision ID: 058_tennis_state_raw_evidence
Revises: 057_event_state_raw_key
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "058_tennis_state_raw_evidence"
down_revision = "057_event_state_raw_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tennis_event_state_raw_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_event_id", sa.String(length=64), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_object_key", sa.String(length=512), nullable=False),
        sa.Column("raw_content_hash", sa.String(length=80), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default="{}"),
        sa.Column("reconstructed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("snapshot_id", "snapshot_observed_at", "evidence_hash",
                            name="uq_tennis_event_state_raw_evidence"),
        sa.CheckConstraint("available_at <= snapshot_observed_at",
                           name="ck_tennis_event_state_raw_evidence_asof"),
    )
    op.create_index(
        "idx_tennis_event_state_raw_evidence_lookup",
        "tennis_event_state_raw_evidence",
        ["snapshot_id", "snapshot_observed_at", "available_at"],
    )
    op.execute("""
        CREATE FUNCTION reject_tennis_event_state_raw_evidence_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'tennis event state raw evidence is append-only';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_tennis_event_state_raw_evidence_immutable
        BEFORE UPDATE OR DELETE ON tennis_event_state_raw_evidence
        FOR EACH ROW EXECUTE FUNCTION reject_tennis_event_state_raw_evidence_mutation()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_tennis_event_state_raw_evidence_immutable ON tennis_event_state_raw_evidence")
    op.execute("DROP FUNCTION IF EXISTS reject_tennis_event_state_raw_evidence_mutation()")
    op.drop_index("idx_tennis_event_state_raw_evidence_lookup", table_name="tennis_event_state_raw_evidence")
    op.drop_table("tennis_event_state_raw_evidence")
