"""Expose dedicated Flashscore executor liveness to the scheduler UI."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "027_flashscore_worker_heartbeat"
down_revision: Union[str, None] = "026_flashscore_parallel_slots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column("flashscore_source_control", sa.Column("executor_heartbeat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("flashscore_source_control", "executor_heartbeat_at")
