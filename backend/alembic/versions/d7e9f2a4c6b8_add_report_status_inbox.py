"""add report.status status_changed_at status_note (inbox draft flow)

Revision ID: d7e9f2a4c6b8
Revises: c3d5e7f9a1b2
Create Date: 2026-08-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7e9f2a4c6b8'
down_revision: Union[str, Sequence[str], None] = 'c3d5e7f9a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """§5.3 draft 流：报告签发状态机。存量行回填 issued（已签发语义）。"""
    with op.batch_alter_table('report', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('status', sa.String(length=10), nullable=False,
                      server_default='issued')
        )
        batch_op.add_column(sa.Column('status_changed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('status_note', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('report', schema=None) as batch_op:
        batch_op.drop_column('status_note')
        batch_op.drop_column('status_changed_at')
        batch_op.drop_column('status')
