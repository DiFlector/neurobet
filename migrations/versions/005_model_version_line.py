"""Version tags are unique per model line, not globally.

Revision ID: 005_model_version_line
Revises: 004_performance_and_compression
Create Date: 2026-10-13 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "005_model_version_line"
down_revision: Union[str, None] = "004_performance_and_compression"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("model_versions_version_tag_key", "model_versions", type_="unique")
    op.create_unique_constraint(
        "uq_model_versions_line",
        "model_versions",
        ["sport_code", "model_name", "market", "version_tag"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_model_versions_line", "model_versions", type_="unique")
    op.create_unique_constraint("model_versions_version_tag_key", "model_versions", ["version_tag"])
