"""Add the composite lookup index used by derive-on-read mastery queries.

Revision ID: a7c4e9f2b6d1
Revises: f7b2d9e4a6c8
Create Date: 2026-09-13 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a7c4e9f2b6d1"
down_revision: Union[str, Sequence[str], None] = "f7b2d9e4a6c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_evidence_event_student_kp_occurred",
        "evidence_event",
        ["student_id", "kp_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_event_student_kp_occurred", table_name="evidence_event")
