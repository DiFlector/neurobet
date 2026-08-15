"""prediction verdict: model decides win/loss instead of a fixed EV/confidence cutoff

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-15

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE live.ai_predictions
            ADD COLUMN predicted_win INTEGER,
            ADD COLUMN decision_confidence DOUBLE PRECISION;
    """)

    op.execute("""
        ALTER TABLE finished.finished_bets
            ADD COLUMN predicted_win INTEGER;
    """)

    # Backfill: before this migration, "the model bet on it" meant calibrated
    # win_probability >= min_confidence (70) — see backend/main.py's old
    # get_top_neurobets min_confidence gate. Reconstruct the equivalent verdict for
    # historical rows so guess-rate stats have something to look at immediately
    # instead of starting from an empty slate. Rows the model never scored
    # (predicted_win_probability IS NULL) stay NULL — genuinely unjudged, not a guess.
    op.execute("""
        UPDATE finished.finished_bets
           SET predicted_win = CASE WHEN predicted_win_probability >= 70 THEN 1 ELSE 0 END
         WHERE predicted_win_probability IS NOT NULL;
    """)

    op.execute("CREATE INDEX idx_finished_bets_verdict ON finished.finished_bets (predicted_win, is_win);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS finished.idx_finished_bets_verdict;")
    op.execute("ALTER TABLE finished.finished_bets DROP COLUMN IF EXISTS predicted_win;")
    op.execute("""
        ALTER TABLE live.ai_predictions
            DROP COLUMN IF EXISTS predicted_win,
            DROP COLUMN IF EXISTS decision_confidence;
    """)
