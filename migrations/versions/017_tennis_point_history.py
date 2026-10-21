"""Add immutable point history and fencing tokens for tennis refresh jobs."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "017_tennis_point_history"
down_revision: Union[str, None] = "016_tennis_player_refresh_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tennis_player_refresh_jobs", sa.Column("attempt_token", sa.String(36), nullable=True))
    op.create_table(
        "tennis_point_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="flashscore"),
        sa.Column("source_match_id", sa.String(128), nullable=False),
        sa.Column("source_url", sa.String(512), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("archived_path", sa.String(512), nullable=True),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrospective", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("parse_status", sa.String(32), nullable=False),
        sa.Column("quality_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("idx_tennis_point_document_asof", "tennis_point_documents", ["source_match_id", "source_url", "retrieved_at"])
    op.create_table(
        "tennis_point_history_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tennis_point_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_match_id", sa.String(128), nullable=False),
        sa.Column("fonbet_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="SET NULL"), nullable=True),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("events_json", postgresql.JSONB(), nullable=False),
        sa.Column("quality_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("retrospective", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("idx_tennis_point_history_event_time", "tennis_point_history_versions", ["fonbet_event_id", "retrieved_at"])
    op.create_table(
        "tennis_point_match_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_match_id", sa.String(128), nullable=False),
        sa.Column("fonbet_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="unmatched"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tennis_point_match_links")
    op.drop_index("idx_tennis_point_history_event_time", table_name="tennis_point_history_versions")
    op.drop_table("tennis_point_history_versions")
    op.drop_index("idx_tennis_point_document_asof", table_name="tennis_point_documents")
    op.drop_table("tennis_point_documents")
    op.drop_column("tennis_player_refresh_jobs", "attempt_token")
