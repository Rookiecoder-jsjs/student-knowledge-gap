"""存量库增量迁移（create_all 轨）：Intervention.retest_exam_id（progress-loop-design P2）。

幂等：列已存在跳过。alembic 轨由 e5a9c7b3d1f4 迁移承担，本脚本仅服务
create_all 分支的既有库——与 add_rbac_scope_columns 同纪律（增量列 INTEGER
不加 FK，引用完整性靠应用层）。
"""

from __future__ import annotations

import sqlalchemy as sa

from app.db import engine


def add_retest_exam_column() -> None:
    insp = sa.inspect(engine)
    with engine.begin() as conn:
        cols = {c["name"] for c in insp.get_columns("intervention")}
        if "retest_exam_id" not in cols:
            conn.execute(
                sa.text("ALTER TABLE intervention ADD COLUMN retest_exam_id INTEGER")
            )


if __name__ == "__main__":
    add_retest_exam_column()
    print("intervention.retest_exam_id ready")
