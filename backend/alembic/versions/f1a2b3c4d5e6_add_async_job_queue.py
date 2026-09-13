"""add durable async job queue

Revision ID: f1a2b3c4d5e6
Revises: a7c4e9f2b6d1
"""

from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "a7c4e9f2b6d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "async_job",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("locked_by", sa.String(length=100), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "idempotency_key", name="uq_async_job_idempotency"),
    )
    op.create_index("ix_async_job_status_available", "async_job", ["status", "available_at"])
    op.create_index("ix_async_job_available_at", "async_job", ["available_at"])


def downgrade() -> None:
    op.drop_index("ix_async_job_available_at", table_name="async_job")
    op.drop_index("ix_async_job_status_available", table_name="async_job")
    op.drop_table("async_job")
