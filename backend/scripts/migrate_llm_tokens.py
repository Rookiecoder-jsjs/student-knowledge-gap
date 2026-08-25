"""一次性迁移：为 llm_call_log 加 token 计数两列（§5.9 用量台账 v1）。

幂等：列已存在则跳过。create_all 轨的存量库由此补列；alembic 轨由
e8f1a3b5d7c9 迁移承担（init_db 单点隔离）。

用法：
    cd backend && python -m scripts.migrate_llm_tokens
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402


def add_token_columns() -> bool:
    """若 llm_call_log 缺 token 两列则补齐。返回是否新增。"""
    from app.db import engine

    cols = {c["name"] for c in inspect(engine).get_columns("llm_call_log")}
    missing = []
    if "prompt_tokens" not in cols:
        missing.append("ALTER TABLE llm_call_log ADD COLUMN prompt_tokens INTEGER")
    if "completion_tokens" not in cols:
        missing.append("ALTER TABLE llm_call_log ADD COLUMN completion_tokens INTEGER")
    if not missing:
        print("[migrate] llm_call_log token 列已存在，跳过")
        return False
    with engine.begin() as conn:
        for stmt in missing:
            conn.execute(text(stmt))
    print(f"[migrate] 已为 llm_call_log 添加 {len(missing)} 列")
    return True


def main() -> None:
    from app.db import init_db

    init_db()
    add_token_columns()
    print("[migrate] 完成")


if __name__ == "__main__":
    main()
