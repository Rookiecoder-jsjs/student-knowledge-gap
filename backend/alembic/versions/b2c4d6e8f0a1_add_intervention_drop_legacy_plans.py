"""add intervention table; drop legacy intervention_plan/plan_item/retest_outcome

Revision ID: b2c4d6e8f0a1
Revises: e8f1a3b5d7c9
Create Date: 2026-08-25 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c4d6e8f0a1'
down_revision: Union[str, Sequence[str], None] = 'e8f1a3b5d7c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """干预闭环 v1：新 intervention 表（执行事实）；清退零引用遗留三元组。

    intervention_plan/plan_item/retest_outcome 自初始 schema 起无任何代码消费方，
    且 retest_outcome 存掌握度快照违反不变量②——由 derive-on-read 的
    intervention_effect 取代，效果不落快照。
    """
    op.drop_table('retest_outcome')
    op.drop_table('plan_item')
    op.drop_table('intervention_plan')

    op.create_table(
        'intervention',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('class_id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=True),
        sa.Column('kp_id', sa.Integer(), nullable=False),
        sa.Column('exam_id', sa.Integer(), nullable=False),
        sa.Column('attribution_id', sa.Integer(), nullable=True),
        sa.Column('source_report_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('scope', sa.String(length=12), nullable=False),
        sa.Column('group_ref', sa.String(length=40), nullable=True),
        sa.Column('baseline_as_of', sa.DateTime(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('suggested_at', sa.DateTime(), nullable=False),
        sa.Column('done_at', sa.DateTime(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['attribution_id'], ['attribution.id'], ),
        sa.ForeignKeyConstraint(['class_id'], ['class.id'], ),
        sa.ForeignKeyConstraint(['exam_id'], ['exam_template.id'], ),
        sa.ForeignKeyConstraint(['kp_id'], ['knowledge_point.id'], ),
        sa.ForeignKeyConstraint(['source_report_id'], ['report.id'], ),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_intervention_class_status', 'intervention', ['class_id', 'status'])
    op.create_index('ix_intervention_student', 'intervention', ['student_id'])
    op.create_index('ix_intervention_exam', 'intervention', ['exam_id'])


def downgrade() -> None:
    op.drop_index('ix_intervention_exam', table_name='intervention')
    op.drop_index('ix_intervention_student', table_name='intervention')
    op.drop_index('ix_intervention_class_status', table_name='intervention')
    op.drop_table('intervention')
    # 复辟遗留三表（G10 downgrade-to-base 要求逐版可回滚；形状照抄初始 schema）
    op.create_table(
        'intervention_plan',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('approved_by', sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'plan_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('kp_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=20), nullable=False),
        sa.Column('bank_question_id', sa.Integer(), nullable=True),
        sa.Column('due', sa.Date(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['bank_question_id'], ['bank_question.id'], ),
        sa.ForeignKeyConstraint(['kp_id'], ['knowledge_point.id'], ),
        sa.ForeignKeyConstraint(['plan_id'], ['intervention_plan.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'retest_outcome',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_item_id', sa.Integer(), nullable=False),
        sa.Column('mastery_before', sa.Float(), nullable=False),
        sa.Column('mastery_after', sa.Float(), nullable=False),
        sa.Column('improved', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['plan_item_id'], ['plan_item.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
