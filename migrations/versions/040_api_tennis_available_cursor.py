"""Index API-Tennis observations for correction-aware archive checkpoints.

Revision ID: 040_api_tennis_available_cursor
Revises: 039_api_tennis_player_id
Create Date: 2026-10-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "040_api_tennis_available_cursor"
down_revision: Union[str, None] = "039_api_tennis_player_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_api_tennis_observation_available_cursor "
            "ON ext_tennis_match_observations (available_at, source_match_id) "
            "WHERE source = 'api_tennis'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_api_tennis_observation_available_cursor")
