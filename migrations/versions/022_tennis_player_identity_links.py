"""Add append-only confirmed tennis player identity links."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "022_tennis_player_identity_links"
down_revision: Union[str, None] = "021_flashscore_jobs_parse_times"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tennis_player_identity_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("participant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("participants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_player_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_tennis_player_identity_asof", "tennis_player_identity_links",
                    ["participant_id", "source", "available_at"])
    op.create_index("idx_tennis_player_identity_source", "tennis_player_identity_links",
                    ["source", "source_player_id", "available_at"])


def downgrade() -> None:
    op.drop_index("idx_tennis_player_identity_source", table_name="tennis_player_identity_links")
    op.drop_index("idx_tennis_player_identity_asof", table_name="tennis_player_identity_links")
    op.drop_table("tennis_player_identity_links")
