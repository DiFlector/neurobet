"""Expire abandoned Flashscore queue entries after owner process loss."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "025_source_queue_heartbeat"
down_revision: Union[str, None] = "024_flashscore_priority_queue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "flashscore_source_requests",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "idx_flashscore_source_requests_heartbeat",
        "flashscore_source_requests",
        ["heartbeat_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_flashscore_source_requests_heartbeat", table_name="flashscore_source_requests")
    op.drop_column("flashscore_source_requests", "heartbeat_at")
