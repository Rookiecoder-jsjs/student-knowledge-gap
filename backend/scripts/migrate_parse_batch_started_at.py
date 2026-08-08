"""一次性迁移：为 parse_batch_item 加 started_at 列（G6 看门狗计时基准）。

幂等：列已存在则跳过。配套 runtime-goals.md G6。

用法：
    cd backend && python -m scripts.migrate_parse_batch_started_at
    或 PYTHONPATH=backend python scripts/migrate_parse_batch_started_at.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 直接运行脚本时确保能 import app
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

from app.db import init_db  # noqa: E402


def add_started_at_column() -> bool:
    """若 parse_batch_item 无 started_at 列则加上。返回是否新增。

    create_all 只建缺失的表、不给已有表加列，故存量库需此 ALTER。
    函数内取 engine，使 startup 调用时用到当前（测试可替换的）engine。
    """
    from app.db import engine

    cols = {c["name"] for c in inspect(engine).get_columns("parse_batch_item")}
    if "started_at" in cols:
        print("[migrate] parse_batch_item.started_at 列已存在，跳过")
        return False
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE parse_batch_item ADD COLUMN started_at DATETIME"))
    print("[migrate] 已添加 parse_batch_item.started_at 列")
    return True


def main() -> None:
    init_db()
    add_started_at_column()
    print("[migrate] 完成")


if __name__ == "__main__":
    main()
