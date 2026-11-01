"""retain archived Fonbet source key on tennis event snapshots

Revision ID: 057_event_state_raw_key
Revises: 056_tennis_event_attrs
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa


revision = "057_event_state_raw_key"
down_revision = "056_tennis_event_attrs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "event_state_snapshots",
        sa.Column("raw_object_key", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("event_state_snapshots", "raw_object_key")
