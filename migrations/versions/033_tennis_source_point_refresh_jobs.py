"""Add source-keyed durable Flashscore PBP refresh jobs."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "033_source_point_jobs"
down_revision: Union[str, None] = "032_metric_identity_provenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tennis_point_source_refresh_jobs",
        sa.Column("source_match_id", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_token", sa.String(length=36), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_error", sa.String(length=128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("source_match_id"),
    )
    op.create_index(
        "idx_tennis_point_source_refresh_dispatch",
        "tennis_point_source_refresh_jobs",
        ["status", "next_run_at", "lease_until"],
    )


def downgrade() -> None:
    op.drop_index("idx_tennis_point_source_refresh_dispatch", table_name="tennis_point_source_refresh_jobs")
    op.drop_table("tennis_point_source_refresh_jobs")
