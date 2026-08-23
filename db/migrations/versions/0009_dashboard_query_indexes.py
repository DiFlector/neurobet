"""Indexes for homepage history / live-bet reads.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-23

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_finished_bets_finished_at "
        "ON finished.finished_bets (finished_at DESC, id DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_finished_events_sport_top "
        "ON finished.finished_events (LOWER(TRIM(SPLIT_PART(sport_path, '/', 1))));"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_live_bets_status_id "
        "ON finished.live_bets (status, id DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS finished.idx_live_bets_status_id;")
    op.execute("DROP INDEX IF EXISTS finished.idx_finished_events_sport_top;")
    op.execute("DROP INDEX IF EXISTS finished.idx_finished_bets_finished_at;")
