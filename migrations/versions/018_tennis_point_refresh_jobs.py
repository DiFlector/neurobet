"""Persist live point-history refresh jobs."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "018_tennis_point_refresh_jobs"
down_revision: Union[str, None] = "017_tennis_point_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table("tennis_point_refresh_jobs",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_token", sa.String(36), nullable=True),
        sa.Column("last_requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_error", sa.String(128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_tennis_point_refresh_dispatch", "tennis_point_refresh_jobs", ["status", "next_run_at", "lease_until"])

def downgrade() -> None:
    op.drop_index("idx_tennis_point_refresh_dispatch", table_name="tennis_point_refresh_jobs")
    op.drop_table("tennis_point_refresh_jobs")
