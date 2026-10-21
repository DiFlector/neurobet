"""Persist odds-free tennis forecast snapshots.

Revision ID: 011_tennis_forecasts
Revises: 010_external_predictions
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "011_tennis_forecasts"
down_revision: Union[str, None] = "010_external_predictions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tennis_forecast_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("feature_version", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state_digest", sa.String(64), nullable=False),
        sa.Column("forecast_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_id", "model_name", "model_version", "state_digest", name="uq_tennis_forecast_state"),
    )
    op.create_index("idx_tennis_forecasts_event_time", "tennis_forecast_snapshots", ["event_id", "observed_at"])
    op.create_index("idx_tennis_forecasts_model_time", "tennis_forecast_snapshots", ["model_name", "model_version", "observed_at"])


def downgrade() -> None:
    op.drop_index("idx_tennis_forecasts_model_time", table_name="tennis_forecast_snapshots")
    op.drop_index("idx_tennis_forecasts_event_time", table_name="tennis_forecast_snapshots")
    op.drop_table("tennis_forecast_snapshots")
