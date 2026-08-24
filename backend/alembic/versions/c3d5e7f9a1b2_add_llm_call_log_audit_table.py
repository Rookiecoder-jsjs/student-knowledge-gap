"""add llm_call_log audit table

Revision ID: c3d5e7f9a1b2
Revises: b9f2a1c4d7e8
Create Date: 2026-08-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d5e7f9a1b2'
down_revision: Union[str, Sequence[str], None] = 'b9f2a1c4d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """LLM 调用全程审计表（rollout 思想：append-only，只增不改）。"""
    op.create_table(
        'llm_call_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('at', sa.DateTime(), nullable=True),
        sa.Column('capability', sa.String(length=20), nullable=False),
        sa.Column('task', sa.String(length=40), nullable=False),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('prompt_version', sa.String(length=30), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('input_sha256', sa.String(length=64), nullable=False),
        sa.Column('input_chars', sa.Integer(), nullable=False),
        sa.Column('has_image', sa.Boolean(), nullable=False),
        sa.Column('response_json', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_llm_call_log_at'), 'llm_call_log', ['at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_llm_call_log_at'), table_name='llm_call_log')
    op.drop_table('llm_call_log')
