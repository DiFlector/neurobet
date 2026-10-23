"""Record the confirmed source identity used for each player metric snapshot."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "032_metric_identity_provenance"
down_revision: Union[str, None] = "031_serve_quality"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tennis_player_metric_snapshots",
        sa.Column("source_player_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tennis_player_metric_snapshots", "source_player_id")
