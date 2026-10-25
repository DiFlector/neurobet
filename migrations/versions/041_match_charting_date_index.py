"""Index Match Charting Project metadata dates for bounded PBP linking.

Revision ID: 041_match_charting_date_index
Revises: 040_api_tennis_available_cursor
Create Date: 2026-10-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "041_match_charting_date_index"
down_revision: Union[str, None] = "040_api_tennis_available_cursor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_mcp_observation_payload_date "
            "ON ext_tennis_match_observations ((payload->>'date'), source_match_id) "
            "WHERE source = 'match_charting_project'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_mcp_observation_payload_date")
