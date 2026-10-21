"""Keep immutable versions of externally sourced tennis results.

Revision ID: 012_tennis_match_observations
Revises: 011_tennis_forecasts
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "012_tennis_match_observations"
down_revision: Union[str, None] = "011_tennis_forecasts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ext_tennis_match_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_match_id", sa.String(128), nullable=False),
        sa.Column("observation_hash", sa.String(64), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.String(512), nullable=False),
        sa.Column("source_content_hash", sa.String(80), nullable=True),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source", "source_match_id", "observation_hash", name="uq_ext_tennis_match_observation"),
    )
    op.create_index(
        "idx_ext_tennis_match_observation_time",
        "ext_tennis_match_observations",
        ["source", "source_match_id", "available_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_ext_tennis_match_observation_time", table_name="ext_tennis_match_observations")
    op.drop_table("ext_tennis_match_observations")
