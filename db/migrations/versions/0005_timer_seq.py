"""timer_seq: match-elapsed-time per odds snapshot, so the GRU can tell "odds dropped
on minute 5" from "odds dropped on minute 85" instead of only seeing the odds/score
curve with no absolute point-in-match to anchor it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-16

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # live.odds_history gets the raw timer string per snapshot (Fonbet's live `timer`
    # field — see backend/parser_service.py's _extract_live_score_and_timer; it's a
    # free-text field, not guaranteed to be a clock, so it's stored raw and parsed
    # defensively at archive time, same division of labor as score_at_time).
    op.execute("""
        ALTER TABLE live.odds_history
            ADD COLUMN timer_at_time TEXT;
    """)
    # finished.finished_bets gets the already-parsed elapsed-seconds sequence (or null
    # per entry when a snapshot's timer string wasn't clock-shaped) — same pattern as
    # score_seq_json. Nullable, no backfill: the per-snapshot odds_history rows this
    # would be reconstructed from are deleted at archive time.
    op.execute("""
        ALTER TABLE finished.finished_bets
            ADD COLUMN timer_seq_json TEXT;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE finished.finished_bets DROP COLUMN IF EXISTS timer_seq_json;")
    op.execute("ALTER TABLE live.odds_history DROP COLUMN IF EXISTS timer_at_time;")
