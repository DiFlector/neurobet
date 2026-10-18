"""External predictions pre-match storage with 24h TTL and matching engine.

Revision ID: 010_external_predictions
Revises: 009_validation_details
Create Date: 2026-10-18 20:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "010_external_predictions"
down_revision: Union[str, None] = "009_validation_details"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_predictions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("source_code", sa.String(64), nullable=False),
        sa.Column("source_url", sa.String(512), nullable=False),
        sa.Column("sport_code", sa.String(32), server_default="tennis", nullable=False),
        sa.Column("tournament_name", sa.String(255), server_default="", nullable=False),
        sa.Column("raw_participant_a", sa.String(255), nullable=False),
        sa.Column("raw_participant_b", sa.String(255), nullable=False),
        sa.Column("canonical_participant_a", sa.String(255), nullable=False),
        sa.Column("canonical_participant_b", sa.String(255), nullable=False),
        sa.Column("estimated_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market_type", sa.String(64), server_default="match_winner", nullable=False),
        sa.Column("predicted_outcome", sa.String(64), nullable=False),
        sa.Column("raw_prediction_text", sa.String(255), nullable=False),
        sa.Column("recommended_odds", sa.Numeric(10, 4), nullable=True),
        sa.Column("author_name", sa.String(128), nullable=True),
        sa.Column("author_roi", sa.Numeric(8, 2), nullable=True),
        sa.Column("author_winrate", sa.Numeric(6, 2), nullable=True),
        sa.Column("reasoning_snippet", sa.String(2048), nullable=True),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("matched_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="SET NULL"), nullable=True),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_url", name="uq_external_predictions_source_url"),
    )

    op.create_index(
        "idx_ext_pred_canonical",
        "external_predictions",
        ["canonical_participant_a", "canonical_participant_b", "estimated_start_at"],
    )
    op.create_index(
        "idx_ext_pred_matched_event",
        "external_predictions",
        ["matched_event_id"],
    )
    op.create_index(
        "idx_ext_pred_status_expires",
        "external_predictions",
        ["status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_ext_pred_status_expires", table_name="external_predictions")
    op.drop_index("idx_ext_pred_matched_event", table_name="external_predictions")
    op.drop_index("idx_ext_pred_canonical", table_name="external_predictions")
    op.drop_table("external_predictions")
