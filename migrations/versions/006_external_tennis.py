"""External tennis history and alias confidence.

Revision ID: 006_external_tennis
Revises: 005_model_version_line
Create Date: 2026-10-14 19:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006_external_tennis"
down_revision: Union[str, None] = "005_model_version_line"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("participant_aliases", sa.Column("link_confidence", sa.Float(), nullable=True))
    op.create_table(
        "ext_tennis_players",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_player_id", sa.String(64), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("gender", sa.String(8), server_default="unknown", nullable=False),
        sa.Column("hand", sa.String(8), nullable=True),
        sa.Column("ioc", sa.String(8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source", "source_player_id", name="uq_ext_tennis_players_source"),
    )
    op.create_index("idx_ext_tennis_players_name", "ext_tennis_players", ["full_name"])
    op.create_table(
        "ext_tennis_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_match_id", sa.String(128), nullable=False),
        sa.Column("played_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tour_level", sa.String(32), server_default="unknown", nullable=False),
        sa.Column("tournament", sa.String(255), server_default="", nullable=False),
        sa.Column("surface", sa.String(32), server_default="unknown", nullable=False),
        sa.Column("best_of", sa.Integer(), server_default="3", nullable=False),
        sa.Column("round_name", sa.String(32), server_default="", nullable=False),
        sa.Column("gender", sa.String(8), server_default="unknown", nullable=False),
        sa.Column("winner_name", sa.String(255), nullable=False),
        sa.Column("loser_name", sa.String(255), nullable=False),
        sa.Column("winner_player_id", sa.String(64), nullable=True),
        sa.Column("loser_player_id", sa.String(64), nullable=True),
        sa.Column("winner_rank", sa.Integer(), nullable=True),
        sa.Column("loser_rank", sa.Integer(), nullable=True),
        sa.Column("score", sa.String(64), server_default="", nullable=False),
        sa.Column("odds_winner", sa.Float(), nullable=True),
        sa.Column("odds_loser", sa.Float(), nullable=True),
        sa.Column("serve_won_winner", sa.Float(), nullable=True),
        sa.Column("serve_won_loser", sa.Float(), nullable=True),
        sa.Column("return_won_winner", sa.Float(), nullable=True),
        sa.Column("return_won_loser", sa.Float(), nullable=True),
        sa.Column("stats", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source", "source_match_id", name="uq_ext_tennis_matches_source"),
    )
    op.create_index("idx_ext_tennis_matches_available", "ext_tennis_matches", ["available_at"])
    op.create_index("idx_ext_tennis_matches_players", "ext_tennis_matches", ["winner_name", "loser_name"])
    op.create_table(
        "ext_tennis_rankings",
        sa.Column("tour", sa.String(8), primary_key=True),
        sa.Column("ranking_date", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("source_player_id", sa.String(64), primary_key=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("points", sa.Float(), nullable=True),
    )
    op.create_table(
        "ext_tennis_serve_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_match_id", sa.String(160), nullable=False),
        sa.Column("player_name", sa.String(255), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("surface", sa.String(32), server_default="unknown", nullable=False),
        sa.Column("serve_points", sa.Float(), nullable=False),
        sa.Column("serve_points_won", sa.Float(), nullable=False),
        sa.UniqueConstraint("source", "source_match_id", "player_name", name="uq_ext_tennis_serve_log"),
    )
    op.create_index("idx_ext_tennis_serve_player", "ext_tennis_serve_logs", ["player_name", "available_at"])


def downgrade() -> None:
    op.drop_index("idx_ext_tennis_serve_player", table_name="ext_tennis_serve_logs")
    op.drop_table("ext_tennis_serve_logs")
    op.drop_table("ext_tennis_rankings")
    op.drop_index("idx_ext_tennis_matches_players", table_name="ext_tennis_matches")
    op.drop_index("idx_ext_tennis_matches_available", table_name="ext_tennis_matches")
    op.drop_table("ext_tennis_matches")
    op.drop_index("idx_ext_tennis_players_name", table_name="ext_tennis_players")
    op.drop_table("ext_tennis_players")
    op.drop_column("participant_aliases", "link_confidence")
