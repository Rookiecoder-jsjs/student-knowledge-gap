"""一次性迁移：为 knowledge_point 加 archived 列，并把最新 kb_version 置 active。

幂等：列已存在则跳过；已有 active 版本则不动 status。配套 kb-edit §3.1/§3.2。

用法：
    cd backend && python -m scripts.migrate_kb_archived
    或 PYTHONPATH=backend python scripts/migrate_kb_archived.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 直接运行脚本时确保能 import app
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, select, text  # noqa: E402

from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.models import KbVersion  # noqa: E402


def add_archived_column() -> bool:
    """若 knowledge_point 无 archived 列则加上。返回是否新增。

    create_all 只建缺失的表、不给已有表加列，故存量库需此 ALTER。
    函数内取 engine，使 startup 调用时用到当前（测试可替换的）engine。
    """
    from app.db import engine

    cols = {c["name"] for c in inspect(engine).get_columns("knowledge_point")}
    if "archived" in cols:
        print("[migrate] knowledge_point.archived 列已存在，跳过")
        return False
    # SQLite 存 BOOLEAN 为 0/1；PG 用 FALSE。默认非空归档位 = False(0)。
    default = "FALSE" if not engine.dialect.name.startswith("sqlite") else "0"
    with engine.begin() as conn:
        conn.execute(
            text(
                f"ALTER TABLE knowledge_point "
                f"ADD COLUMN archived BOOLEAN NOT NULL DEFAULT {default}"
            )
        )
    print("[migrate] 已添加 knowledge_point.archived 列")
    return True


def ensure_active_version() -> bool:
    """若无 active 版本，把最新 kb_version 置 active。返回是否置位。

    _active_kb 改为按 status 取后，老库（全部 draft）会找不到 active -> 兜底取最新。
    此处显式置位，让语义清晰，避免依赖兜底。
    """
    with SessionLocal() as s:
        active = s.scalar(select(KbVersion).where(KbVersion.status == "active"))
        if active is not None:
            print(f"[migrate] 已存在 active 版本 kb#{active.id}，跳过")
            return False
        latest = s.scalar(select(KbVersion).order_by(KbVersion.id.desc()))
        if latest is None:
            print("[migrate] 库中无知识库版本，跳过")
            return False
        latest.status = "active"
        s.commit()
        print(f"[migrate] 已把最新版本 kb#{latest.id} 置 active")
        return True


def main() -> None:
    # 先建表：create_all 只加缺失的表（空库会直接建带 archived 的表，无需 ALTER）。
    init_db()
    add_archived_column()
    ensure_active_version()
    print("[migrate] 完成")


if __name__ == "__main__":
    main()
