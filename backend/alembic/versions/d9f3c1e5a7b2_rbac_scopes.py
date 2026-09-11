"""RBAC 范围体系：考试学科/版本年级/班主任/科任学科/学科管理员表（rbac-scopes-design §3）

Revision ID: d9f3c1e5a7b2
Revises: b8d0e2f4a6c1
Create Date: 2026-09-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9f3c1e5a7b2'
down_revision: Union[str, Sequence[str], None] = 'b8d0e2f4a6c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """四列一表 + 存量考试学科回填。

    - homeroom_teacher_id 加 INTEGER 而非 FK：与既有增量列纪律一致（SQLite ALTER
      不补 FK，引用完整性靠应用层）；create_all 新库由模型层带 FK。
    - exam_template.subject 回填 ← class.subject：存量考试视为本班默认学科语境
      （多学科暗缝只对新录入考试生效，存量行为不变）。
    """
    with op.batch_alter_table('exam_template', schema=None) as batch_op:
        batch_op.add_column(sa.Column('subject', sa.String(length=20), nullable=True))
    with op.batch_alter_table('kb_version', schema=None) as batch_op:
        batch_op.add_column(sa.Column('grade', sa.Integer(), nullable=True))
    with op.batch_alter_table('class', schema=None) as batch_op:
        batch_op.add_column(sa.Column('homeroom_teacher_id', sa.Integer(), nullable=True))
    with op.batch_alter_table('teacher_class', schema=None) as batch_op:
        batch_op.add_column(sa.Column('subject', sa.String(length=20), nullable=True))

    op.create_table(
        'teacher_subject_scope',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('teacher_id', sa.Integer(), nullable=False),
        sa.Column('subject', sa.String(length=20), nullable=False),
        sa.Column('grade', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['teacher_id'], ['teacher.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('teacher_id', 'subject', 'grade', name='uq_teacher_subject_scope'),
    )

    op.execute(
        "UPDATE exam_template SET subject = "
        "(SELECT c.subject FROM class c WHERE c.id = exam_template.class_id) "
        "WHERE subject IS NULL"
    )


def downgrade() -> None:
    op.drop_table('teacher_subject_scope')
    with op.batch_alter_table('teacher_class', schema=None) as batch_op:
        batch_op.drop_column('subject')
    with op.batch_alter_table('class', schema=None) as batch_op:
        batch_op.drop_column('homeroom_teacher_id')
    with op.batch_alter_table('kb_version', schema=None) as batch_op:
        batch_op.drop_column('grade')
    with op.batch_alter_table('exam_template', schema=None) as batch_op:
        batch_op.drop_column('subject')
