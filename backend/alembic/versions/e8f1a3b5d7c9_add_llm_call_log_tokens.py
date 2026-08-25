"""add llm_call_log prompt_tokens completion_tokens (usage ledger)

Revision ID: e8f1a3b5d7c9
Revises: d7e9f2a4c6b8
Create Date: 2026-08-25 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8f1a3b5d7c9'
down_revision: Union[str, Sequence[str], None] = 'd7e9f2a4c6b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """§5.9 用量台账 v1：token 计数两列（历史行 NULL=无计量）。"""
    with op.batch_alter_table('llm_call_log', schema=None) as batch_op:
        batch_op.add_column(sa.Column('prompt_tokens', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('completion_tokens', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('llm_call_log', schema=None) as batch_op:
        batch_op.drop_column('completion_tokens')
        batch_op.drop_column('prompt_tokens')
