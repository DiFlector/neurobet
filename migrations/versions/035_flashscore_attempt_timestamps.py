"""Track the start time of the current Flashscore queue attempt."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "035_flashscore_attempts"
down_revision: Union[str, None] = "034_ext_tennis_source_played"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = (
    "tennis_player_refresh_jobs",
    "flashscore_import_jobs",
    "tennis_point_refresh_jobs",
    "tennis_match_lookup_jobs",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("attempt_started_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("""
        UPDATE tennis_player_refresh_jobs
        SET attempt_started_at = updated_at
        WHERE status = 'running' AND attempt_started_at IS NULL
    """)
    op.execute("""
        UPDATE flashscore_import_jobs
        SET attempt_started_at = updated_at
        WHERE status = 'running' AND attempt_started_at IS NULL
    """)
    op.execute("""
        UPDATE tennis_point_refresh_jobs
        SET attempt_started_at = updated_at
        WHERE status = 'running' AND attempt_started_at IS NULL
    """)
    op.execute("""
        UPDATE tennis_match_lookup_jobs
        SET attempt_started_at = updated_at
        WHERE status = 'running' AND attempt_started_at IS NULL
    """)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "attempt_started_at")
