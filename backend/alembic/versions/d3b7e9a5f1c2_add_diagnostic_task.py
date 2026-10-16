"""Persist the target and source of individual diagnostic tasks."""

from alembic import op
import sqlalchemy as sa

revision = "d3b7e9a5f1c2"
down_revision = "c2a6f8d4e0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "diagnostic_task",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("exam_template.id"), nullable=False, unique=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("kp_id", sa.Integer(), sa.ForeignKey("knowledge_point.id"), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("attribution_id", sa.Integer(), sa.ForeignKey("attribution.id"), nullable=True),
        sa.Column("source_question_ids_json", sa.JSON(), nullable=False),
        sa.Column("preview_token", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("diagnostic_task")
