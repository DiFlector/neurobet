"""Persist player retry scheduling and Fonbet-to-Flashscore match lookup jobs."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "028_flashscore_search_retries"
down_revision: Union[str, None] = "027_flashscore_worker_heartbeat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tennis_player_refresh_jobs", sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE tennis_player_refresh_jobs SET next_run_at = COALESCE(requested_at, now())")
    op.alter_column("tennis_player_refresh_jobs", "next_run_at", nullable=False, server_default=sa.func.now())
    op.drop_index("idx_tennis_player_refresh_dispatch", table_name="tennis_player_refresh_jobs")
    op.create_index("idx_tennis_player_refresh_dispatch", "tennis_player_refresh_jobs", ["status", "next_run_at", "lease_until"])
    op.create_table(
        "tennis_match_lookup_jobs",
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("events.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_token", sa.String(length=36), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("source_match_id", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.String(length=128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_tennis_match_lookup_dispatch", "tennis_match_lookup_jobs", ["status", "next_run_at", "lease_until"])


def downgrade() -> None:
    op.drop_index("idx_tennis_match_lookup_dispatch", table_name="tennis_match_lookup_jobs")
    op.drop_table("tennis_match_lookup_jobs")
    op.drop_index("idx_tennis_player_refresh_dispatch", table_name="tennis_player_refresh_jobs")
    op.create_index("idx_tennis_player_refresh_dispatch", "tennis_player_refresh_jobs", ["status", "requested_at", "lease_until"])
    op.drop_column("tennis_player_refresh_jobs", "next_run_at")
