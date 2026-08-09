"""active 知识库解析（架构修复 候选5a：统一 routes.py / auto_generate.py 两处 _active_kb 的分叉）。

职责边界（seam）：
- 领域层：返回 ``KbVersion | None``；strict 模式下无 active 版本抛 ``KbNotActiveError``。
- HTTP 层（api/）负责把领域信号翻译成 HTTPException；
- 报告层拿 ``None`` 跳过生成（best-effort），不因知识库未激活而失败。

原两处实现行为分叉（routes 抛 HTTPException + strict；auto_generate 返回 None 无 strict），
此处收为单一策略；消费方各自决定「报错」还是「跳过」。
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KbVersion

logger = logging.getLogger(__name__)


class KbNotActiveError(RuntimeError):
    """strict 模式下无 status=active 的知识库版本（improvement-plan §2.1）。"""


def strict_active() -> bool:
    """SC_KB_STRICT_ACTIVE=1|true|yes 时，分析层不得兜底 draft。"""
    return os.environ.get("SC_KB_STRICT_ACTIVE", "").lower() in ("1", "true", "yes")


def active_kb(session: Session) -> KbVersion | None:
    """取 status=active 的最新版本；无 active 时按 strict 决定兜底或抛错。

    - strict（SC_KB_STRICT_ACTIVE）无 active → 抛 ``KbNotActiveError``，
      避免分析跑在未审图谱上；
    - 否则兜底最新版本并 warning（结论需教研核对，与运行时一致）；
    - 仍无任何版本 → 返回 ``None``。
    """
    kb = session.scalar(
        select(KbVersion)
        .where(KbVersion.status == "active")
        .order_by(KbVersion.id.desc())
    )
    if kb is not None:
        return kb
    if strict_active():
        raise KbNotActiveError(
            "无审核通过(active)的知识库版本，请先审核并激活（SC_KB_STRICT_ACTIVE 已开启）"
        )
    kb = session.scalar(select(KbVersion).order_by(KbVersion.id.desc()))
    if kb is not None and kb.status != "active":
        logger.warning(
            "分析层兜底使用未激活的知识库版本(id=%d, status=%s, %s v%s)，"
            "该版本未经教研审核，归因结果需谨慎核对（improvement-plan §2.1）",
            kb.id,
            kb.status,
            kb.textbook_edition,
            kb.version,
        )
    return kb
