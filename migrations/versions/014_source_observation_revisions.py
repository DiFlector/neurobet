"""Allow a source result to return to an earlier value after a correction.

Revision ID: 014_source_observation_revisions
Revises: 013_tennis_player_metrics
"""
from typing import Sequence, Union

from alembic import op

revision: str = "014_source_observation_revisions"
down_revision: Union[str, None] = "013_tennis_player_metrics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_ext_tennis_match_observation", "ext_tennis_match_observations", type_="unique")
    op.create_unique_constraint(
        "uq_ext_tennis_match_observation_version",
        "ext_tennis_match_observations",
        ["source", "source_match_id", "available_at"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_ext_tennis_match_observation_version", "ext_tennis_match_observations", type_="unique")
    op.create_unique_constraint(
        "uq_ext_tennis_match_observation",
        "ext_tennis_match_observations",
        ["source", "source_match_id", "observation_hash"],
    )
