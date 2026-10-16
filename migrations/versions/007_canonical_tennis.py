"""Canonical tennis matches and players.

Revision ID: 007_canonical_tennis
Revises: 006_external_tennis
Create Date: 2026-10-15 00:20:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007_canonical_tennis"
down_revision: Union[str, None] = "006_external_tennis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ext_tennis_canonical_players",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("gender", sa.String(8), server_default="unknown", nullable=False),
        sa.Column("hand", sa.String(8), nullable=True),
        sa.Column("ioc", sa.String(8), nullable=True),
        sa.Column("surname_key", sa.String(128), server_default="", nullable=False),
        sa.Column("initial", sa.String(8), server_default="", nullable=False),
        sa.Column("source_ids", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "idx_ext_tennis_canonical_players_name",
        "ext_tennis_canonical_players",
        ["surname_key", "initial", "gender"],
    )
    op.create_table(
        "ext_tennis_canonical_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("canonical_key", sa.String(64), nullable=False),
        sa.Column("played_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tour_level", sa.String(32), server_default="other", nullable=False),
        sa.Column("tournament", sa.String(255), server_default="", nullable=False),
        sa.Column("surface", sa.String(32), server_default="unknown", nullable=False),
        sa.Column("best_of", sa.Integer(), server_default="3", nullable=False),
        sa.Column("round_name", sa.String(32), server_default="", nullable=False),
        sa.Column("gender", sa.String(8), server_default="unknown", nullable=False),
        sa.Column("winner_name", sa.String(255), nullable=False),
        sa.Column("loser_name", sa.String(255), nullable=False),
        sa.Column("winner_player_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("loser_player_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("winner_rank", sa.Integer(), nullable=True),
        sa.Column("loser_rank", sa.Integer(), nullable=True),
        sa.Column("score", sa.String(64), server_default="", nullable=False),
        sa.Column("odds_winner", sa.Float(), nullable=True),
        sa.Column("odds_loser", sa.Float(), nullable=True),
        sa.Column("serve_won_winner", sa.Float(), nullable=True),
        sa.Column("serve_won_loser", sa.Float(), nullable=True),
        sa.Column("return_won_winner", sa.Float(), nullable=True),
        sa.Column("return_won_loser", sa.Float(), nullable=True),
        sa.Column("odds_source", sa.String(32), nullable=True),
        sa.Column("stats_source", sa.String(32), nullable=True),
        sa.Column("stats", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("canonical_key", name="uq_ext_tennis_canonical_key"),
    )
    op.create_index("idx_ext_tennis_canonical_available", "ext_tennis_canonical_matches", ["available_at"])
    op.create_index(
        "idx_ext_tennis_canonical_players",
        "ext_tennis_canonical_matches",
        ["winner_player_id", "loser_player_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_ext_tennis_canonical_players", table_name="ext_tennis_canonical_matches")
    op.drop_index("idx_ext_tennis_canonical_available", table_name="ext_tennis_canonical_matches")
    op.drop_table("ext_tennis_canonical_matches")
    op.drop_index("idx_ext_tennis_canonical_players_name", table_name="ext_tennis_canonical_players")
    op.drop_table("ext_tennis_canonical_players")
