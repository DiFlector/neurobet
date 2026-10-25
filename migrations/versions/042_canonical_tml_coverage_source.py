"""Canonicalize historical TennisMyLife coverage aliases.

Revision ID: 042_tml_coverage_source
Revises: 041_match_charting_date_index
Create Date: 2026-10-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "042_tml_coverage_source"
down_revision: Union[str, None] = "041_match_charting_date_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Older serve-stat materialization wrote the internal `tml` alias. Merge
    # it into the provider's canonical key while retaining the newest evidence.
    op.execute("""
        INSERT INTO tennis_player_data_coverage (
            participant_id, source, capability, checked_at, source_watermark,
            status, complete, evidence_json
        )
        SELECT participant_id, 'tennis_my_life', capability, checked_at,
               source_watermark, status, complete, evidence_json
        FROM tennis_player_data_coverage
        WHERE source = 'tml'
        ON CONFLICT (participant_id, source, capability) DO UPDATE
        SET checked_at = EXCLUDED.checked_at,
            source_watermark = EXCLUDED.source_watermark,
            status = EXCLUDED.status,
            complete = EXCLUDED.complete,
            evidence_json = EXCLUDED.evidence_json
        WHERE tennis_player_data_coverage.checked_at <= EXCLUDED.checked_at
    """)
    op.execute("DELETE FROM tennis_player_data_coverage WHERE source = 'tml'")


def downgrade() -> None:
    # The alias rows are merged with canonical provider rows, so they cannot be
    # losslessly split back without discarding newer source evidence.
    pass
