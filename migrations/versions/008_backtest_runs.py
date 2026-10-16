"""Historical replay jobs, stored apart from the production ledger.

Revision ID: 008_backtest_runs
Revises: 007_canonical_tennis
Create Date: 2026-10-16 08:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008_backtest_runs"
down_revision: Union[str, None] = "007_canonical_tennis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("sport_code", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("request_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("error", sa.String(2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_backtest_runs_status_created", "backtest_runs", ["status", "created_at"])
    op.create_index("idx_backtest_runs_sport_created", "backtest_runs", ["sport_code", "created_at"])
    op.create_table(
        "backtest_run_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("final_balance", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("roi", sa.Numeric(10, 4), nullable=True),
        sa.Column("bankruptcies", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_backtest_run_results_run", "backtest_run_results", ["run_id"])


def downgrade() -> None:
    op.drop_index("idx_backtest_run_results_run", table_name="backtest_run_results")
    op.drop_table("backtest_run_results")
    op.drop_index("idx_backtest_runs_sport_created", table_name="backtest_runs")
    op.drop_index("idx_backtest_runs_status_created", table_name="backtest_runs")
    op.drop_table("backtest_runs")
