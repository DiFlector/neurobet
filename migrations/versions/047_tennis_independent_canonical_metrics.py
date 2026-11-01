"""Persist causal canonical-player metrics and ranking observations.

Revision ID: 047_tennis_independent_canonical_metrics
Revises: 046_external_tennis_player_id_indexes
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "047_tennis_ind_metrics"
down_revision: Union[str, None] = "046_external_player_id_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tennis_canonical_metric_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("canonical_player_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ext_tennis_canonical_players.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("metrics_version", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_watermark", sa.DateTime(timezone=True), nullable=True),
        sa.Column("history_digest", sa.String(64), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("canonical_player_id", "metrics_version", "history_digest", name="uq_tennis_canonical_metric_snapshot"),
    )
    op.create_index("idx_tennis_canonical_metric_asof", "tennis_canonical_metric_snapshots", ["canonical_player_id", "available_at"])

    op.create_table(
        "tennis_player_ranking_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_player_id", sa.String(128), nullable=False),
        sa.Column("canonical_player_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ext_tennis_canonical_players.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tour", sa.String(8), nullable=False),
        sa.Column("ranking_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("points", sa.Float(), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_ref", sa.String(512), nullable=False),
        sa.Column("source_content_hash", sa.String(80), nullable=False),
        sa.Column("is_singles", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("tour IN ('atp', 'wta')", name="ck_tennis_ranking_snapshot_tour"),
        sa.CheckConstraint("rank > 0", name="ck_tennis_ranking_snapshot_rank"),
        sa.CheckConstraint("is_singles", name="ck_tennis_ranking_snapshot_singles"),
        sa.UniqueConstraint("source", "source_player_id", "tour", "ranking_date", "available_at", name="uq_tennis_ranking_observation"),
    )
    op.create_index("idx_tennis_ranking_asof", "tennis_player_ranking_snapshots", ["canonical_player_id", "tour", "available_at"])



def downgrade() -> None:
    op.drop_index("idx_tennis_ranking_asof", table_name="tennis_player_ranking_snapshots")
    op.drop_table("tennis_player_ranking_snapshots")
    op.drop_index("idx_tennis_canonical_metric_asof", table_name="tennis_canonical_metric_snapshots")
    op.drop_table("tennis_canonical_metric_snapshots")
