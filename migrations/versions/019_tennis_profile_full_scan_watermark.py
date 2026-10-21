"""Track monthly full Flashscore profile scans separately from incremental refreshes."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "019_tennis_full_scan"
down_revision: Union[str, None] = "018_tennis_point_refresh_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("tennis_player_profiles", sa.Column("last_full_history_scan_at", sa.DateTime(timezone=True), nullable=True))

def downgrade() -> None:
    op.drop_column("tennis_player_profiles", "last_full_history_scan_at")
