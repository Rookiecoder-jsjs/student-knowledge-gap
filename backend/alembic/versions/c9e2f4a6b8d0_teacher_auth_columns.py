"""teacher 凭据列 + teacher_class 授权表（G11，agent-product-design §5.5）

Revision ID: c9e2f4a6b8d0
Revises: b2c4d6e8f0a1
Create Date: 2026-08-25 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9e2f4a6b8d0'
down_revision: Union[str, Sequence[str], None] = 'b2c4d6e8f0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """G11 鉴权地基：教师登录凭据三列（皆可空=未启用）+ 教师↔班级多对多。"""
    with op.batch_alter_table('teacher', schema=None) as batch_op:
        batch_op.add_column(sa.Column('username', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('password_hash', sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column('salt', sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column(
            'admin', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.create_unique_constraint('uq_teacher_username', ['username'])

    op.create_table(
        'teacher_class',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('teacher_id', sa.Integer(), nullable=False),
        sa.Column('class_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['class_id'], ['class.id']),
        sa.ForeignKeyConstraint(['teacher_id'], ['teacher.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('teacher_id', 'class_id', name='uq_teacher_class'),
    )


def downgrade() -> None:
    op.drop_table('teacher_class')
    with op.batch_alter_table('teacher', schema=None) as batch_op:
        batch_op.drop_constraint('uq_teacher_username', type_='unique')
        batch_op.drop_column('admin')
        batch_op.drop_column('salt')
        batch_op.drop_column('password_hash')
        batch_op.drop_column('username')
