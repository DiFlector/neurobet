"""Add expiring priority queue entries for serialized Flashscore access."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "024_flashscore_priority_queue"
down_revision: Union[str, None] = "023_tennis_result_fact_validity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flashscore_source_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_flashscore_source_requests_priority",
        "flashscore_source_requests",
        ["priority", "enqueued_at", "id"],
    )
    op.create_index(
        "idx_flashscore_source_requests_lease",
        "flashscore_source_requests",
        ["lease_until"],
    )


def downgrade() -> None:
    op.drop_index("idx_flashscore_source_requests_lease", table_name="flashscore_source_requests")
    op.drop_index("idx_flashscore_source_requests_priority", table_name="flashscore_source_requests")
    op.drop_table("flashscore_source_requests")
