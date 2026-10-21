"""Persist player identities, deduplicated result facts, and metric versions.

Revision ID: 013_tennis_player_metrics
Revises: 012_tennis_match_observations
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "013_tennis_player_metrics"
down_revision: Union[str, None] = "012_tennis_match_observations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tennis_player_profiles",
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("canonical_player_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ext_tennis_canonical_players.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_identities", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("profile_url", sa.String(512), nullable=True),
        sa.Column("sync_status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("sync_error", sa.String(64), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_watermark", sa.DateTime(timezone=True), nullable=True),
        sa.Column("history_match_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("metrics_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("history_digest", sa.String(64), nullable=True),
        sa.Column("metrics_available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_tennis_player_profiles_canonical", "tennis_player_profiles", ["canonical_player_id"])
    op.create_index("idx_tennis_player_profiles_sync", "tennis_player_profiles", ["sync_status", "last_attempt_at"])

    op.create_table(
        "tennis_player_metric_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("participants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metrics_version", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_watermark", sa.DateTime(timezone=True), nullable=True),
        sa.Column("history_digest", sa.String(64), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("participant_id", "metrics_version", "history_digest", name="uq_tennis_player_metric_snapshot"),
    )
    op.create_index("idx_tennis_player_metric_asof", "tennis_player_metric_snapshots", ["participant_id", "available_at"])

    op.create_table(
        "tennis_player_result_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("participants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_match_id", sa.String(128), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("played_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("surface", sa.String(32), server_default="unknown", nullable=False),
        sa.Column("tour_level", sa.String(32), server_default="unknown", nullable=False),
        sa.Column("won", sa.Boolean(), nullable=False),
        sa.Column("sets_played", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("serve_points", sa.Float(), nullable=True),
        sa.Column("serve_points_won", sa.Float(), nullable=True),
        sa.Column("source_payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("participant_id", "source", "source_match_id", "available_at", name="uq_tennis_player_result_fact_version"),
    )
    op.create_index("idx_tennis_player_fact_time", "tennis_player_result_facts", ["participant_id", "played_at", "available_at"])


def downgrade() -> None:
    op.drop_index("idx_tennis_player_fact_time", table_name="tennis_player_result_facts")
    op.drop_table("tennis_player_result_facts")
    op.drop_index("idx_tennis_player_metric_asof", table_name="tennis_player_metric_snapshots")
    op.drop_table("tennis_player_metric_snapshots")
    op.drop_index("idx_tennis_player_profiles_sync", table_name="tennis_player_profiles")
    op.drop_index("idx_tennis_player_profiles_canonical", table_name="tennis_player_profiles")
    op.drop_table("tennis_player_profiles")
