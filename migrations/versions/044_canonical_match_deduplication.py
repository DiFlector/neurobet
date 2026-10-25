"""Retain cross-source canonical match duplicates without counting them twice.

Revision ID: 044_canonical_match_dedupe
Revises: 043_canonical_player_aliases
Create Date: 2026-10-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "044_canonical_match_dedupe"
down_revision: Union[str, None] = "043_canonical_player_aliases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ext_tennis_canonical_matches "
        "ADD COLUMN IF NOT EXISTS duplicate_of_id UUID"
    )
    # Both constraints are NOT VALID to avoid scanning the large historical
    # table while holding an ACCESS EXCLUSIVE lock. PostgreSQL still enforces
    # them for new and updated rows; the new nullable column contains no links.
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_ext_tennis_canonical_match_duplicate_of'
                  AND conrelid = 'ext_tennis_canonical_matches'::regclass
            ) THEN
                ALTER TABLE ext_tennis_canonical_matches
                ADD CONSTRAINT fk_ext_tennis_canonical_match_duplicate_of
                FOREIGN KEY (duplicate_of_id)
                REFERENCES ext_tennis_canonical_matches(id)
                ON DELETE RESTRICT NOT VALID;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_ext_tennis_match_not_self_duplicate'
                  AND conrelid = 'ext_tennis_canonical_matches'::regclass
            ) THEN
                ALTER TABLE ext_tennis_canonical_matches
                ADD CONSTRAINT ck_ext_tennis_match_not_self_duplicate
                CHECK (duplicate_of_id IS NULL OR duplicate_of_id <> id) NOT VALID;
            END IF;
        END $$;
    """)
    # Concurrent index builds permit readers and writers during the historical
    # table scan. IF NOT EXISTS makes a partially completed run resumable.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ext_tennis_canonical_played_gender "
            "ON ext_tennis_canonical_matches (played_at, gender)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ext_tennis_canonical_duplicate_of "
            "ON ext_tennis_canonical_matches (duplicate_of_id) "
            "WHERE duplicate_of_id IS NOT NULL"
        )


def downgrade() -> None:
    op.drop_index("idx_ext_tennis_canonical_duplicate_of", table_name="ext_tennis_canonical_matches")
    op.drop_index("idx_ext_tennis_canonical_played_gender", table_name="ext_tennis_canonical_matches")
    op.drop_constraint("ck_ext_tennis_match_not_self_duplicate", "ext_tennis_canonical_matches", type_="check")
    op.drop_constraint("fk_ext_tennis_canonical_match_duplicate_of", "ext_tennis_canonical_matches", type_="foreignkey")
    op.drop_column("ext_tennis_canonical_matches", "duplicate_of_id")
