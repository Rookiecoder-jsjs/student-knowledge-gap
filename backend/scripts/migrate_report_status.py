"""一次性迁移：为 report 加签发状态三列（§5.3 收件箱 draft 流）。

幂等：列已存在则跳过。create_all 轨的存量库由此补列；alembic 轨由
d7e9f2a4c6b8 迁移承担（init_db 单点隔离，两轨互不重复执行）。

用法：
    cd backend && python -m scripts.migrate_report_status
"""

from __future__ import annotations

import sys
from pathlib import Path

# 直接运行脚本时确保能 import app
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402


def add_status_columns() -> bool:
    """若 report 缺 status 三列则补齐。返回是否新增。

    函数内取 engine，使 startup 调用时用到当前（测试可替换的）engine。
    """
    from app.db import engine

    cols = {c["name"] for c in inspect(engine).get_columns("report")}
    missing: list[str] = []
    if "status" not in cols:
        missing.append("ALTER TABLE report ADD COLUMN status VARCHAR(10) DEFAULT 'issued' NOT NULL")
    if "status_changed_at" not in cols:
        missing.append("ALTER TABLE report ADD COLUMN status_changed_at DATETIME")
    if "status_note" not in cols:
        missing.append("ALTER TABLE report ADD COLUMN status_note TEXT")
    if not missing:
        print("[migrate] report 状态列已存在，跳过")
        return False
    with engine.begin() as conn:
        for stmt in missing:
            conn.execute(text(stmt))
    print(f"[migrate] 已为 report 添加 {len(missing)} 列")
    return True


def main() -> None:
    from app.db import init_db

    init_db()
    add_status_columns()
    print("[migrate] 完成")


if __name__ == "__main__":
    main()
