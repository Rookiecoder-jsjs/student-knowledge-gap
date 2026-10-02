"""Persist intervention retest schedule, preserving existing execution history."""

from alembic import op
import sqlalchemy as sa

revision = "c2a6f8d4e0b1"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("intervention", sa.Column("retest_due_date", sa.Date(), nullable=True))
    op.add_column("exam_response", sa.Column("source_warnings_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("exam_response", "source_warnings_json")
    op.drop_column("intervention", "retest_due_date")
