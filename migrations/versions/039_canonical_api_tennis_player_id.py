"""Index API-Tennis aliases used by the incremental canonical projector.

Revision ID: 039_api_tennis_player_id
Revises: 038_canonical_loser_idx
Create Date: 2026-10-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "039_api_tennis_player_id"
down_revision: Union[str, None] = "038_canonical_loser_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ext_tennis_canonical_players_api_id "
            "ON ext_tennis_canonical_players ((source_ids->>'api_tennis')) "
            "WHERE source_ids ? 'api_tennis'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_ext_tennis_canonical_players_api_id")
