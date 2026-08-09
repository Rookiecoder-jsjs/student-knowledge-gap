"""add exam_id and narrative_markdown to report

Revision ID: b9f2a1c4d7e8
Revises: 2548b632b722
Create Date: 2026-08-09 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9f2a1c4d7e8'
down_revision: Union[str, Sequence[str], None] = '2548b632b722'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """报告关联考试 + 缓存 AI 解读段。SQLite 用 batch 模式才能加外键。"""
    with op.batch_alter_table('report', schema=None) as batch_op:
        batch_op.add_column(sa.Column('exam_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('narrative_markdown', sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            'fk_report_exam_id', 'exam_template', ['exam_id'], ['id']
        )


def downgrade() -> None:
    with op.batch_alter_table('report', schema=None) as batch_op:
        batch_op.drop_constraint('fk_report_exam_id', type_='foreignkey')
        batch_op.drop_column('narrative_markdown')
        batch_op.drop_column('exam_id')
