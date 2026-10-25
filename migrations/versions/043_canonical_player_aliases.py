"""Persist multiple source player IDs per canonical identity.

Revision ID: 043_canonical_player_aliases
Revises: 042_tml_coverage_source
Create Date: 2026-10-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "043_canonical_player_aliases"
down_revision: Union[str, None] = "042_tml_coverage_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ext_tennis_canonical_player_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_player_id", sa.String(length=128), nullable=False),
        sa.Column("canonical_player_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="confirmed", nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('confirmed', 'candidate', 'rejected')", name="ck_ext_tennis_player_alias_status"),
        sa.ForeignKeyConstraint(
            ["canonical_player_id"], ["ext_tennis_canonical_players.id"],
            ondelete="RESTRICT", name="fk_ext_tennis_player_alias_canonical",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ext_tennis_canonical_player_aliases"),
        sa.UniqueConstraint(
            "source", "source_player_id", "canonical_player_id",
            name="uq_ext_tennis_canonical_player_alias_candidate",
        ),
    )
    op.create_index(
        "idx_ext_tennis_player_alias_lookup", "ext_tennis_canonical_player_aliases",
        ["source", "source_player_id", "status"],
    )
    op.create_index(
        "idx_ext_tennis_player_alias_person", "ext_tennis_canonical_player_aliases",
        ["canonical_player_id", "source", "status"],
    )
    op.create_index(
        "uq_ext_tennis_player_alias_confirmed", "ext_tennis_canonical_player_aliases",
        ["source", "source_player_id"], unique=True, postgresql_where=sa.text("status = 'confirmed'"),
    )

    # Preserve every existing mapping. Reused provider IDs are copied as
    # candidates for all owners, never arbitrarily promoted to a person.
    op.execute("""
        WITH expanded AS (
            SELECT cp.id AS canonical_player_id, source_ids.key AS source,
                   source_ids.value AS source_player_id,
                   count(*) OVER (PARTITION BY source_ids.key, source_ids.value) AS owners
            FROM ext_tennis_canonical_players cp
            CROSS JOIN LATERAL jsonb_each_text(COALESCE(cp.source_ids, '{}'::jsonb)) source_ids
            WHERE NULLIF(source_ids.value, '') IS NOT NULL
        )
        INSERT INTO ext_tennis_canonical_player_aliases (
            id, source, source_player_id, canonical_player_id, status, evidence_json
        )
        SELECT gen_random_uuid(), source, source_player_id, canonical_player_id,
               CASE WHEN owners = 1 THEN 'confirmed' ELSE 'candidate' END,
               jsonb_build_object('method','legacy_source_ids_backfill','legacy_owner_count',owners)
        FROM expanded
        ON CONFLICT (source, source_player_id, canonical_player_id) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("ext_tennis_canonical_player_aliases")
