"""teacher.kb_editor 布尔列（知识库授权编辑，frontend-ends 两层写权）

Revision ID: b8d0e2f4a6c1
Revises: a1f3b5d7e9c0
Create Date: 2026-09-10 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8d0e2f4a6c1'
down_revision: Union[str, Sequence[str], None] = 'a1f3b5d7e9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """教师知识库编辑授权（默认 False：未授权只读；admin 天然含写权不受此列约束）。"""
    with op.batch_alter_table('teacher', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('kb_editor', sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table('teacher', schema=None) as batch_op:
        batch_op.drop_column('kb_editor')
