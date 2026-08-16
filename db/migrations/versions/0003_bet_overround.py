"""bet overround: market-group vig (sum of 1/coeff across sibling outcomes) captured
at archive time for the core main-outcome markets, so the LightGBM leg can learn from
the bookmaker's own margin instead of only ever seeing a single outcome's coefficient
in isolation.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-16

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, no backfill: reconstructing this for already-archived bets would need
    # sibling-factor odds_history that's long since been deleted (archive_finished_events
    # drops odds_history for an event once it's archived) — old rows just stay NULL and
    # the LightGBM feature builder treats that as "no info" (neutral 1.0), same pattern
    # as score_seq_json's backward-compat fallback.
    op.execute("""
        ALTER TABLE finished.finished_bets
            ADD COLUMN overround_close DOUBLE PRECISION;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE finished.finished_bets DROP COLUMN IF EXISTS overround_close;")
