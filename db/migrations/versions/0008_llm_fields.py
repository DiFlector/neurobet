"""LLM rationale/context columns on live.ai_predictions.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-20

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE live.ai_predictions
            ADD COLUMN IF NOT EXISTS llm_rationale TEXT,
            ADD COLUMN IF NOT EXISTS llm_context JSONB;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE live.ai_predictions
            DROP COLUMN IF EXISTS llm_rationale,
            DROP COLUMN IF EXISTS llm_context;
    """)
