"""存量库增量迁移（create_all 轨）：student 凭据列（三角色登录，auth-roles-design）。

幂等：列已存在则跳过。alembic 轨由对应迁移承担，本脚本仅服务 create_all 分支的
既有库——与 _legacy_alter_bootstrap 其他成员同纪律（teacher 见 add_teacher_auth）。

注：存量库 ALTER 无法补列级 UNIQUE（sqlite）；新库由模型 unique=True 在 CREATE
TABLE 落约束，与 teacher 凭据列的既有取舍一致——查重靠应用层预检兜底。
"""

from __future__ import annotations

import sqlalchemy as sa

from app.db import engine


def add_student_auth() -> None:
    insp = sa.inspect(engine)
    cols = {c["name"] for c in insp.get_columns("student")}
    with engine.begin() as conn:
        if "username" not in cols:
            conn.execute(sa.text("ALTER TABLE student ADD COLUMN username VARCHAR(64)"))
        if "password_hash" not in cols:
            conn.execute(sa.text("ALTER TABLE student ADD COLUMN password_hash BLOB"))
        if "salt" not in cols:
            conn.execute(sa.text("ALTER TABLE student ADD COLUMN salt BLOB"))


if __name__ == "__main__":
    add_student_auth()
    print("student auth columns ready")
