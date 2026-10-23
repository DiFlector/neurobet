"""Persist explicit serve-log source confidence and retrospective status."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "031_serve_quality"
down_revision: Union[str, None] = "030_tennis_serve_log_provenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ext_tennis_serve_logs",
        sa.Column("source_confidence", sa.String(length=24), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "ext_tennis_serve_logs",
        sa.Column("retrospective", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("ext_tennis_serve_logs", "retrospective")
    op.drop_column("ext_tennis_serve_logs", "source_confidence")
