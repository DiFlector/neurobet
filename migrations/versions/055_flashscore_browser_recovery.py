"""Coordinate local Flashscore recovery across research processes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "055_flashscore_recovery"
down_revision = "054_tennis_point_doc_status"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "flashscore_browser_recovery",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("lease_owner", sa.Text()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("session_id", sa.Text()),
        sa.Column("session_expires_at", sa.DateTime(timezone=True)),
        sa.Column("egress_key", sa.Text()),
        sa.Column("last_result", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id=1", name="ck_flashscore_recovery_singleton"),
    )
    op.execute("INSERT INTO flashscore_browser_recovery (id) VALUES (1)")


def downgrade():
    op.drop_table("flashscore_browser_recovery")
