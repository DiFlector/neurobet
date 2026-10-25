"""Track source freshness by player and independently usable data capability."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "037_tennis_player_data_coverage"
down_revision: Union[str, None] = "036_external_tennis_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tennis_player_data_coverage",
        sa.Column("participant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source", sa.String(32), primary_key=True),
        sa.Column("capability", sa.String(32), primary_key=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_watermark", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index(
        "idx_tennis_player_data_coverage_fresh",
        "tennis_player_data_coverage",
        ["participant_id", "capability", "checked_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_tennis_player_data_coverage_fresh", table_name="tennis_player_data_coverage")
    op.drop_table("tennis_player_data_coverage")
