"""Separate charted match dates from source availability timestamps."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "030_tennis_serve_log_provenance"
down_revision: Union[str, None] = "029_manual_confirmations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ext_tennis_serve_logs",
        sa.Column("source_event_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ext_tennis_serve_logs",
        sa.Column("legacy_available_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ext_tennis_serve_logs",
        sa.Column(
            "availability_provenance",
            sa.String(length=64),
            nullable=False,
            server_default="database_ingest_time",
        ),
    )
    # The legacy charting importer stored match-date + one day as available_at.
    # Preserve that value for audit, retain the source event date separately,
    # and make the records usable only from this correction/migration onward.
    op.execute(sa.text("""
        UPDATE ext_tennis_serve_logs
        SET legacy_available_at = available_at,
            source_event_at = CASE
                WHEN source = 'charting' THEN available_at - INTERVAL '1 day'
                ELSE NULL
            END,
            available_at = now(),
            availability_provenance = 'legacy_corrected_at_migration'
    """))


def downgrade() -> None:
    # Keep the corrected availability timestamp on downgrade; restoring the
    # legacy date would reintroduce the point-in-time leakage this fixes.
    op.drop_column("ext_tennis_serve_logs", "availability_provenance")
    op.drop_column("ext_tennis_serve_logs", "legacy_available_at")
    op.drop_column("ext_tennis_serve_logs", "source_event_at")
