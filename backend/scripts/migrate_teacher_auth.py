"""存量库增量迁移（create_all 轨）：teacher 凭据列 + teacher_class 表（G11）。

幂等：列/表已存在则跳过。alembic 轨由 c9e2f4a6b8d0 承担，本脚本仅服务
create_all 分支的既有库——与 _legacy_alter_bootstrap 其他成员同纪律。
"""

from __future__ import annotations

import sqlalchemy as sa

from app.db import engine


def add_teacher_auth() -> None:
    insp = sa.inspect(engine)
    cols = {c["name"] for c in insp.get_columns("teacher")}
    with engine.begin() as conn:
        if "username" not in cols:
            conn.execute(sa.text("ALTER TABLE teacher ADD COLUMN username VARCHAR(64)"))
        if "password_hash" not in cols:
            conn.execute(sa.text("ALTER TABLE teacher ADD COLUMN password_hash BLOB"))
        if "salt" not in cols:
            conn.execute(sa.text("ALTER TABLE teacher ADD COLUMN salt BLOB"))
        if "admin" not in cols:
            conn.execute(
                sa.text("ALTER TABLE teacher ADD COLUMN admin BOOLEAN NOT NULL DEFAULT 0")
            )
    tables = insp.get_table_names()
    if "teacher_class" not in tables:
        from app import models  # noqa: F401 注册模型
        from app.db import Base as _Base

        _Base.metadata.create_all(engine, tables=["teacher_class"])


if __name__ == "__main__":
    add_teacher_auth()
    print("teacher auth columns ready")
