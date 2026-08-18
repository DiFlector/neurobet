"""timer/overround sequences: per-snapshot bookmaker margin + packed timer
(clock seconds and, for racket sports, current-set point diff).

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-19

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Snapshot-time overround (same sibling set as live inference). Nullable,
    # no backfill: odds_history is deleted at archive, so old rows stay NULL
    # and training treats overround as unknown rather than using closing margin.
    op.execute("""
        ALTER TABLE finished.finished_bets
            ADD COLUMN overround_seq_json TEXT;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE finished.finished_bets DROP COLUMN IF EXISTS overround_seq_json;")
