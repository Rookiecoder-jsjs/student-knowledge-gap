"""遗留干预模型清退（幂等）：drop intervention_plan / plan_item / retest_outcome。

这三张表自初始 schema 起无任何代码消费方，且 retest_outcome 存掌握度快照违反
不变量②（derive-on-read）——由 b2c4d6e8f0a1 迁移（alembic 轨）与本脚本
（create_all 轨）同步清退，效果推导改由 app.intervention.intervention_effect 现算。
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.db import engine

logger = logging.getLogger(__name__)

_LEGACY_TABLES = ("retest_outcome", "plan_item", "intervention_plan")  # 先子后父


def drop_legacy_plan_tables() -> bool:
    """存在则逐个 DROP；返回是否有实际变更。"""
    dropped = False
    with engine.begin() as conn:
        names = set(
            conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).scalars()
        )
        for t in _LEGACY_TABLES:
            if t in names:
                conn.execute(text(f"DROP TABLE {t}"))
                logger.info("[migrate] 遗留表 %s 已删除", t)
                dropped = True
    return dropped


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("dropped" if drop_legacy_plan_tables() else "nothing to drop")
