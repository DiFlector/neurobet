"""Index raw source player IDs for bounded identity re-projection.

Revision ID: 046_external_player_id_idx
Revises: 045_player_alias_decisions
Create Date: 2026-10-24
"""
from typing import Sequence, Union

from alembic import op


revision: str = "046_external_player_id_idx"
down_revision: Union[str, None] = "045_player_alias_decisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The result archive is large and receives ongoing imports. Build indexes
    # without holding a write-blocking table lock for the duration.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_ext_tennis_matches_source_winner_id",
            "ext_tennis_matches",
            ["source", "winner_player_id"],
            postgresql_concurrently=True,
        )
        op.create_index(
            "idx_ext_tennis_matches_source_loser_id",
            "ext_tennis_matches",
            ["source", "loser_player_id"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_ext_tennis_matches_source_loser_id",
            table_name="ext_tennis_matches",
            postgresql_concurrently=True,
        )
        op.drop_index(
            "idx_ext_tennis_matches_source_winner_id",
            table_name="ext_tennis_matches",
            postgresql_concurrently=True,
        )
