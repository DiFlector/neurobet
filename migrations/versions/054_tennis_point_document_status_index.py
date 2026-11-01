"""Cover source status aggregation without reading archived JSON payloads."""

from alembic import op


revision = "054_tennis_point_doc_status"
down_revision = "053_tennis_rank_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_tennis_point_documents_status_cover",
            "tennis_point_documents",
            ["source", "parse_status"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_tennis_point_documents_status_cover",
            table_name="tennis_point_documents",
            postgresql_concurrently=True,
            if_exists=True,
        )
