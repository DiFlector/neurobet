"""Reject mutations to PIT metric and ranking observations.

Revision ID: 048_tennis_pit_immutable
Revises: 047_tennis_ind_metrics
"""
from typing import Sequence, Union

from alembic import op


revision: str = "048_tennis_pit_immutable"
down_revision: Union[str, None] = "047_tennis_ind_metrics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION reject_tennis_pit_snapshot_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'PIT snapshot rows are immutable';
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in ("tennis_canonical_metric_snapshots", "tennis_player_ranking_snapshots"):
        op.execute(f"""
            CREATE TRIGGER trg_{table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_tennis_pit_snapshot_mutation()
        """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_tennis_player_ranking_snapshots_immutable ON tennis_player_ranking_snapshots")
    op.execute("DROP TRIGGER IF EXISTS trg_tennis_canonical_metric_snapshots_immutable ON tennis_canonical_metric_snapshots")
    op.execute("DROP FUNCTION IF EXISTS reject_tennis_pit_snapshot_mutation()")
