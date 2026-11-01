"""Index source-key history lookups without locking ingestion writes."""
from alembic import op
from sqlalchemy import text

revision = "060_point_history_source_idx"
down_revision = "059_statement_metric_dirty"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        valid = op.get_bind().execute(text("""
            SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname='idx_tennis_point_history_source_available'
        """)).scalar_one_or_none()
        if valid is False:
            op.execute("DROP INDEX CONCURRENTLY public.idx_tennis_point_history_source_available")
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tennis_point_history_source_available
            ON public.tennis_point_history_versions (source_match_id,available_at,id)
        """)


def downgrade():
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.idx_tennis_point_history_source_available")
