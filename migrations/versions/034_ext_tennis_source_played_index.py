"""Index Flashscore match lookups by source and played time."""
from typing import Sequence, Union

from alembic import op


revision: str = "034_ext_tennis_source_played"
down_revision: Union[str, None] = "033_source_point_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_ext_tennis_matches_source_played",
            "ext_tennis_matches",
            ["source", "played_at"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_ext_tennis_matches_source_played",
            table_name="ext_tennis_matches",
            postgresql_concurrently=True,
        )
