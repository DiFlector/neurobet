"""Allow a forecast state to recur after an intervening state.

Revision ID: 015_tennis_forecast_revisions
Revises: 014_source_observation_revisions
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "015_tennis_forecast_revisions"
down_revision: Union[str, None] = "014_source_observation_revisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_tennis_forecast_state", "tennis_forecast_snapshots", type_="unique")
    op.add_column("tennis_forecast_snapshots", sa.Column("sequence_no", sa.Integer(), nullable=True))
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY event_id, model_name, model_version
                ORDER BY observed_at, created_at, id
            ) AS sequence_no
            FROM tennis_forecast_snapshots
        )
        UPDATE tennis_forecast_snapshots AS target
        SET sequence_no = ranked.sequence_no
        FROM ranked
        WHERE target.id = ranked.id
    """))
    op.alter_column("tennis_forecast_snapshots", "sequence_no", nullable=False)
    op.create_unique_constraint(
        "uq_tennis_forecast_sequence",
        "tennis_forecast_snapshots",
        ["event_id", "model_name", "model_version", "sequence_no"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_tennis_forecast_sequence", "tennis_forecast_snapshots", type_="unique")
    op.drop_column("tennis_forecast_snapshots", "sequence_no")
    op.create_unique_constraint(
        "uq_tennis_forecast_state",
        "tennis_forecast_snapshots",
        ["event_id", "model_name", "model_version", "state_digest"],
    )
