"""Add resumable external tennis source archive and snapshot manifests."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "036_external_tennis_sources"
down_revision: Union[str, None] = "035_flashscore_attempts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_tennis_source_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("interval_start", sa.Date(), nullable=False),
        sa.Column("interval_end", sa.Date(), nullable=False),
        sa.Column("event_type_key", sa.String(32), nullable=False, server_default=""),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("http_status", sa.Integer()),
        sa.Column("response_bytes", sa.BigInteger()),
        sa.Column("content_sha256", sa.String(64)),
        sa.Column("raw_object_key", sa.String(512)),
        sa.Column("record_count", sa.Integer()),
        sa.Column("point_records", sa.Integer()),
        sa.Column("statistics_records", sa.Integer()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source", "interval_start", "interval_end", "event_type_key", name="uq_ext_tennis_source_interval"),
    )
    op.create_index("idx_ext_tennis_source_dispatch", "external_tennis_source_jobs", ["source", "status", "priority", "next_run_at", "interval_start"])
    op.create_table(
        "external_tennis_source_state",
        sa.Column("source", sa.String(32), primary_key=True),
        sa.Column("state_key", sa.String(96), primary_key=True),
        sa.Column("state_json", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("external_tennis_source_state")
    op.drop_index("idx_ext_tennis_source_dispatch", table_name="external_tennis_source_jobs")
    op.drop_table("external_tennis_source_jobs")
