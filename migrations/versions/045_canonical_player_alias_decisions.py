"""Audit reviewed resolutions of conflicting canonical player aliases.

Revision ID: 045_player_alias_decisions
Revises: 044_canonical_match_dedupe
Create Date: 2026-10-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "045_player_alias_decisions"
down_revision: Union[str, None] = "044_canonical_match_dedupe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ext_tennis_canonical_player_alias_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_player_id", sa.String(length=128), nullable=False),
        sa.Column("canonical_player_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rejected_candidate_ids", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("rationale", sa.String(length=2000), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("decided_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["canonical_player_id"], ["ext_tennis_canonical_players.id"],
            ondelete="RESTRICT", name="fk_ext_tennis_alias_decision_player",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ext_tennis_canonical_player_alias_decisions"),
    )
    op.create_index(
        "idx_ext_tennis_alias_decision_lookup",
        "ext_tennis_canonical_player_alias_decisions",
        ["source", "source_player_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_ext_tennis_alias_decision_lookup", table_name="ext_tennis_canonical_player_alias_decisions")
    op.drop_table("ext_tennis_canonical_player_alias_decisions")
