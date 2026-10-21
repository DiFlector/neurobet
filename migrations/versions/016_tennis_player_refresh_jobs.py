"""Persist and coalesce tennis player history refresh work.

Revision ID: 016_tennis_player_refresh_jobs
Revises: 015_tennis_forecast_revisions
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "016_tennis_player_refresh_jobs"
down_revision: Union[str, None] = "015_tennis_forecast_revisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tennis_player_refresh_jobs",
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(24), server_default="idle", nullable=False),
        sa.Column("requested_reason", sa.String(48), server_default="event", nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("progress_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("last_error", sa.String(128), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_tennis_player_refresh_dispatch", "tennis_player_refresh_jobs", ["status", "requested_at", "lease_until"])


def downgrade() -> None:
    op.drop_index("idx_tennis_player_refresh_dispatch", table_name="tennis_player_refresh_jobs")
    op.drop_table("tennis_player_refresh_jobs")
