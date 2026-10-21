"""Persist immutable point-history refresh status transitions."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "020_point_job_events"
down_revision: Union[str, None] = "019_tennis_full_scan"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tennis_point_refresh_job_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("last_error", sa.String(128), nullable=True),
        sa.Column("source_match_id", sa.String(128), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_tennis_point_job_event_asof",
        "tennis_point_refresh_job_events",
        ["event_id", "available_at"],
    )
    op.execute(sa.text("""
        INSERT INTO tennis_point_refresh_job_events
            (id, event_id, status, last_error, source_match_id, available_at)
        SELECT gen_random_uuid(), event_id, status, last_error, NULL, updated_at
        FROM tennis_point_refresh_jobs
    """))


def downgrade() -> None:
    op.drop_index("idx_tennis_point_job_event_asof", table_name="tennis_point_refresh_job_events")
    op.drop_table("tennis_point_refresh_job_events")
