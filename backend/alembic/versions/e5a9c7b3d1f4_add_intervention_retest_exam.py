"""干预闭环一期 P2：Intervention.retest_exam_id——复测小卷关联事实（progress-loop-design）

Revision ID: e5a9c7b3d1f4
Revises: d9f3c1e5a7b2
Create Date: 2026-09-11 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5a9c7b3d1f4'
down_revision: Union[str, Sequence[str], None] = 'd9f3c1e5a7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """done 干预行的复测卷指针。

    INTEGER 而非 FK：与既有增量列纪律一致（SQLite ALTER 不补 FK，引用完整性
    靠应用层；create_all 新库同形——模型层同样声明为无 FK 的 Integer）。
    """
    with op.batch_alter_table('intervention', schema=None) as batch_op:
        batch_op.add_column(sa.Column('retest_exam_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('intervention', schema=None) as batch_op:
        batch_op.drop_column('retest_exam_id')
