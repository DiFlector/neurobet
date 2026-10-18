"""Audit numbers on a bet validation result.

Revision ID: 009_validation_details
Revises: 008_backtest_runs
Create Date: 2026-10-18 11:20:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "009_validation_details"
down_revision: Union[str, None] = "008_backtest_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bet_validation_results",
        sa.Column("details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("bet_validation_results", "details")
