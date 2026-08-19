"""score_sum trajectory alongside score_diff for total markets.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-20

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE finished.finished_bets
            ADD COLUMN IF NOT EXISTS score_sum_seq_json TEXT;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE finished.finished_bets DROP COLUMN IF EXISTS score_sum_seq_json;")
