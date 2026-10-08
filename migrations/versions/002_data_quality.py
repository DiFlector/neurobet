"""data quality schema: issues table and event quality score

Revision ID: 002_data_quality
Revises: 001_initial_schema
Create Date: 2026-10-08 20:36:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002_data_quality"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add quality_score to events
    op.add_column(
        "events",
        sa.Column("quality_score", sa.Numeric(5, 2), server_default=sa.text("100.00"), nullable=True),
    )

    # 2. Create data_quality_issues table
    op.create_table(
        "data_quality_issues",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=True),
        sa.Column("source_event_id", sa.String(64), nullable=True),
        sa.Column("sport_code", sa.String(32), server_default="tennis", nullable=False),
        sa.Column("issue_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), server_default="WARNING", nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("is_resolved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_dq_issues_event", "data_quality_issues", ["event_id"])
    op.create_index("idx_dq_issues_type", "data_quality_issues", ["issue_type"])
    op.create_index("idx_dq_issues_severity", "data_quality_issues", ["severity"])
    op.create_index("idx_dq_issues_detected", "data_quality_issues", ["detected_at"])


def downgrade() -> None:
    op.drop_table("data_quality_issues")
    op.drop_column("events", "quality_score")
