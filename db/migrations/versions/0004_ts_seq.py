"""ts_seq_json: per-snapshot wall-clock timestamps (epoch seconds) alongside
odds_seq_json/score_seq_json, so training sequences can use real elapsed time between
odds updates instead of the synthetic step-index position (a market that moved 10 times
in 3 minutes and one that moved 10 times over 2 hours used to look identical to the
GRU's time feature).

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-16

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, no backfill — same reasoning as 0003's overround_close: the per-snapshot
    # odds_history rows this would be reconstructed from are deleted at archive time, so
    # old rows just stay NULL and _build_sequence falls back to the positional time
    # feature they were trained with all along.
    op.execute("""
        ALTER TABLE finished.finished_bets
            ADD COLUMN ts_seq_json TEXT;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE finished.finished_bets DROP COLUMN IF EXISTS ts_seq_json;")
