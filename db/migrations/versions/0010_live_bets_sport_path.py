"""Persist sport_path on live_bets so history cards still show the sport after
period-market settlement (match still live) or after the live row is gone.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-28

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE finished.live_bets
            ADD COLUMN IF NOT EXISTS sport_path TEXT NOT NULL DEFAULT '';
    """)
    # Existing rows: prefer the still-live event (period bets settle mid-match),
    # then the archived copy. Empty string stays empty if both are gone.
    op.execute("""
        UPDATE finished.live_bets b
           SET sport_path = e.sport_path
          FROM live.events e
         WHERE b.event_id = e.event_id
           AND COALESCE(b.sport_path, '') = ''
           AND COALESCE(e.sport_path, '') <> '';
    """)
    op.execute("""
        UPDATE finished.live_bets b
           SET sport_path = e.sport_path
          FROM finished.finished_events e
         WHERE b.event_id = e.event_id
           AND COALESCE(b.sport_path, '') = ''
           AND COALESCE(e.sport_path, '') <> '';
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE finished.live_bets DROP COLUMN IF EXISTS sport_path;")
