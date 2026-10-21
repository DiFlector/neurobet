"""Add a durable two-slot and start-rate controller for Flashscore egress."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "026_flashscore_parallel_slots"
down_revision: Union[str, None] = "025_source_queue_heartbeat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("flashscore_source_requests", sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("flashscore_source_requests", sa.Column("slot_number", sa.SmallInteger(), nullable=True))
    op.create_table(
        "flashscore_source_control",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("next_start_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("slot_limit", sa.SmallInteger(), nullable=False, server_default="2"),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_throttle_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 1", name="ck_flashscore_source_control_singleton"),
    )
    op.execute("INSERT INTO flashscore_source_control (id, next_start_at, slot_limit) VALUES (1, now(), 2)")
    op.create_index("idx_flashscore_source_request_slots", "flashscore_source_requests", ["acquired_at", "slot_number"])


def downgrade() -> None:
    op.drop_index("idx_flashscore_source_request_slots", table_name="flashscore_source_requests")
    op.drop_table("flashscore_source_control")
    op.drop_column("flashscore_source_requests", "slot_number")
    op.drop_column("flashscore_source_requests", "acquired_at")
