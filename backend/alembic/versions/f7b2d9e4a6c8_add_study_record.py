"""AI 学习方案 + 学生自报：study_record 表——方案快照 + 自报事实（study-loop-design）

Revision ID: f7b2d9e4a6c8
Revises: e5a9c7b3d1f4
Create Date: 2026-09-11 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7b2d9e4a6c8'
down_revision: Union[str, Sequence[str], None] = 'e5a9c7b3d1f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """学习记录（唯一新表）：create_all 轨由 Base.metadata 建表，本迁移服务 alembic 轨。

    与 Intervention 生命周期同构的事实行——生成落行、自报置时间戳；自报永不
    移动掌握度（不是证据）。intervention_id 为 INTEGER 不加 FK（增量列纪律）。
    """
    op.create_table(
        'study_record',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('class_id', sa.Integer(), sa.ForeignKey('class.id'), nullable=False),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('student.id'), nullable=False),
        sa.Column('kp_id', sa.Integer(), sa.ForeignKey('knowledge_point.id'), nullable=False),
        sa.Column('intervention_id', sa.Integer(), nullable=True),
        sa.Column('plan_markdown', sa.Text(), nullable=False),
        sa.Column('plan_writer', sa.JSON(), nullable=True),
        sa.Column('generated_at', sa.DateTime(), nullable=False),
        sa.Column('self_marked_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index(
        'ix_study_record_student_kp', 'study_record', ['student_id', 'kp_id']
    )


def downgrade() -> None:
    op.drop_index('ix_study_record_student_kp', table_name='study_record')
    op.drop_table('study_record')
