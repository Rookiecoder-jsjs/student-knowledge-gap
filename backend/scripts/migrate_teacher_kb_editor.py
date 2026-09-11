"""存量库增量迁移（create_all 轨）：teacher.kb_editor 布尔列（两层写权）。

幂等：列已存在则跳过。alembic 轨由 b8d0e2f4a6c1 迁移承担，本脚本仅服务
create_all 分支的既有库——与 _legacy_alter_bootstrap 其他成员同纪律
（student 见 add_student_auth）。
"""

from __future__ import annotations

import sqlalchemy as sa

from app.db import engine


def add_teacher_kb_editor() -> None:
    insp = sa.inspect(engine)
    cols = {c["name"] for c in insp.get_columns("teacher")}
    if "kb_editor" in cols:
        return
    with engine.begin() as conn:
        conn.execute(
            sa.text("ALTER TABLE teacher ADD COLUMN kb_editor BOOLEAN NOT NULL DEFAULT 0")
        )


if __name__ == "__main__":
    add_teacher_kb_editor()
    print("teacher kb_editor column ready")
