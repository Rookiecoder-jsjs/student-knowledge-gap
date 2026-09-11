"""student 凭据列（三角色登录：学生自服务账号，auth-roles-design §3）

Revision ID: a1f3b5d7e9c0
Revises: c9e2f4a6b8d0
Create Date: 2026-09-09 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f3b5d7e9c0'
down_revision: Union[str, Sequence[str], None] = 'c9e2f4a6b8d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """学生登录凭据三列（皆可空=未开通登录），镜像 teacher 凭据列。"""
    with op.batch_alter_table('student', schema=None) as batch_op:
        batch_op.add_column(sa.Column('username', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('password_hash', sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column('salt', sa.LargeBinary(), nullable=True))
        batch_op.create_unique_constraint('uq_student_username', ['username'])


def downgrade() -> None:
    with op.batch_alter_table('student', schema=None) as batch_op:
        batch_op.drop_constraint('uq_student_username', type_='unique')
        batch_op.drop_column('salt')
        batch_op.drop_column('password_hash')
        batch_op.drop_column('username')
