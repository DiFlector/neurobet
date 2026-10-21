"""Persist full Flashscore imports and distinguish retrieval from parse availability."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "021_flashscore_jobs_parse_times"
down_revision: Union[str, None] = "020_point_job_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("tennis_point_documents", "tennis_point_history_versions"):
        op.add_column(table, sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("availability_status", sa.String(24), nullable=False,
                                       server_default="unknown_legacy"))
    op.drop_index("idx_tennis_point_history_event_time", table_name="tennis_point_history_versions")
    op.create_index("idx_tennis_point_history_event_available", "tennis_point_history_versions",
                    ["fonbet_event_id", "available_at"])
    op.create_table(
        "flashscore_import_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_key", sa.String(128), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("profile_urls", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("progress_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_token", sa.String(36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_error", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_flashscore_import_dispatch", "flashscore_import_jobs",
                    ["status", "next_run_at", "lease_until"])


def downgrade() -> None:
    op.drop_index("idx_flashscore_import_dispatch", table_name="flashscore_import_jobs")
    op.drop_table("flashscore_import_jobs")
    op.drop_index("idx_tennis_point_history_event_available", table_name="tennis_point_history_versions")
    op.create_index("idx_tennis_point_history_event_time", "tennis_point_history_versions",
                    ["fonbet_event_id", "retrieved_at"])
    for table in ("tennis_point_documents", "tennis_point_history_versions"):
        op.drop_column(table, "availability_status")
        op.drop_column(table, "available_at")
        op.drop_column(table, "parsed_at")
