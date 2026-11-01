"""Add shared provenance and parser-version idempotency to external tennis observations.

Revision ID: 051_external_tennis_ingestion
Revises: 050_tennis_ind_fact_dirty
"""

from alembic import op

revision = "051_external_tennis_ingestion"
down_revision = "050_tennis_ind_fact_dirty"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ext_tennis_match_observations ADD COLUMN IF NOT EXISTS source_document_ref VARCHAR(1024)")
    op.execute("ALTER TABLE ext_tennis_match_observations ADD COLUMN IF NOT EXISTS ingestion_status VARCHAR(24) NOT NULL DEFAULT 'legacy'")
    op.execute("ALTER TABLE ext_tennis_match_observations ADD COLUMN IF NOT EXISTS field_provenance JSONB NOT NULL DEFAULT '{}'::jsonb")
    # This table is large and continuously read. Build the replacement index
    # outside a transaction so PostgreSQL does not block ordinary reads/writes.
    with op.get_context().autocommit_block():
        op.execute("""
          CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_ext_tennis_match_observation_version_v2_idx
          ON ext_tennis_match_observations (source, source_match_id, available_at, observation_hash)
        """)
    op.execute("ALTER TABLE ext_tennis_match_observations DROP CONSTRAINT IF EXISTS uq_ext_tennis_match_observation_version")
    op.execute("""
      ALTER TABLE ext_tennis_match_observations
      ADD CONSTRAINT uq_ext_tennis_match_observation_version
      UNIQUE USING INDEX uq_ext_tennis_match_observation_version_v2_idx
    """)


def downgrade() -> None:
    op.drop_constraint("uq_ext_tennis_match_observation_version", "ext_tennis_match_observations", type_="unique")
    op.create_unique_constraint(
        "uq_ext_tennis_match_observation_version",
        "ext_tennis_match_observations",
        ["source", "source_match_id", "available_at"],
    )
    op.drop_column("ext_tennis_match_observations", "field_provenance")
    op.drop_column("ext_tennis_match_observations", "ingestion_status")
    op.drop_column("ext_tennis_match_observations", "source_document_ref")
