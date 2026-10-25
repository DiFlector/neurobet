"""Index canonical player matches by loser for profile history lookups.

Revision ID: 038_canonical_loser_idx
Revises: 037_tennis_player_data_coverage
Create Date: 2026-10-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "038_canonical_loser_idx"
down_revision: Union[str, None] = "037_tennis_player_data_coverage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ext_tennis_canonical_loser "
            "ON ext_tennis_canonical_matches (loser_player_id, played_at, canonical_key) "
            "WHERE loser_player_id IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_ext_tennis_canonical_loser")
