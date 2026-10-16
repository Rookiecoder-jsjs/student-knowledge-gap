"""Bring migrated knowledge points in line with the current KB model."""

from alembic import op
import sqlalchemy as sa

revision = "e4c8fa6b2d03"
down_revision = "d3b7e9a5f1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_point",
        sa.Column("importance", sa.String(10), nullable=False, server_default="核心"),
    )


def downgrade() -> None:
    op.drop_column("knowledge_point", "importance")
