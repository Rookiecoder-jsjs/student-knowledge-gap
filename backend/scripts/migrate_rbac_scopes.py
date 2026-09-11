"""存量库增量迁移（create_all 轨）：RBAC 范围体系列 + 考试学科回填（rbac-scopes-design §3）。

幂等：列已存在跳过；回填仅处理 subject IS NULL 行。
alembic 轨由 d9f3c1e5a7b2 迁移承担，本脚本仅服务 create_all 分支的既有库——
与 add_teacher_kb_editor 同纪律；teacher_subject_scope 新表由 create_all 免费建，
无需在此处理。
"""

from __future__ import annotations

import sqlalchemy as sa

from app.db import engine


def add_rbac_scope_columns() -> None:
    insp = sa.inspect(engine)
    with engine.begin() as conn:
        cols = {c["name"] for c in insp.get_columns("exam_template")}
        if "subject" not in cols:
            conn.execute(sa.text("ALTER TABLE exam_template ADD COLUMN subject VARCHAR(20)"))
        cols = {c["name"] for c in insp.get_columns("kb_version")}
        if "grade" not in cols:
            conn.execute(sa.text("ALTER TABLE kb_version ADD COLUMN grade INTEGER"))
        cols = {c["name"] for c in insp.get_columns("class")}
        if "homeroom_teacher_id" not in cols:
            conn.execute(sa.text("ALTER TABLE class ADD COLUMN homeroom_teacher_id INTEGER"))
        cols = {c["name"] for c in insp.get_columns("teacher_class")}
        if "subject" not in cols:
            conn.execute(sa.text("ALTER TABLE teacher_class ADD COLUMN subject VARCHAR(20)"))
        # 回填：考试学科 ← 班级默认学科（存量考试视为本班默认学科语境）
        conn.execute(
            sa.text(
                "UPDATE exam_template SET subject = "
                "(SELECT c.subject FROM class c WHERE c.id = exam_template.class_id) "
                "WHERE subject IS NULL"
            )
        )


if __name__ == "__main__":
    add_rbac_scope_columns()
    print("rbac scope columns ready")
