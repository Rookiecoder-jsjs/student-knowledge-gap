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


def active_kb(
    session: Session,
    subject: str | None = None,
    grade: int | None = None,
) -> KbVersion | None:
    """取 status=active 的最新版本（可按学科和年级解析）。

    当调用方知道班级年级时，优先选择 ``KbVersion.grade`` 精确匹配的版本；
    没有精确版本时再回落到该学科的通用版本（``grade IS NULL``）。这让同一
    学科可以同时维护七、八、九年级的 active 图谱，而不会让班级分析串到
    最新年级。无 active 时仍按 strict 策略决定是否允许兜底。

    - subject 给定：在该学科内解析（调用方经 ``Class.subject`` 传入）——学科内
      无 active 时 strict 抛错 / 非严格兜底**同学科**最新版本并 warning，绝不
      跨学科兜底（否则数学分析会串到语文图谱上）；
    - subject 为 None：保持旧行为（全局最新 active），兼容无学科上下文的调用
      （KB 浏览类端点缺省、题干推荐等）。
    - strict（SC_KB_STRICT_ACTIVE）无 active → 抛 ``KbNotActiveError``，避免
      分析跑在未审图谱上；仍无任何版本 → 返回 ``None``。
    """
    stmt = select(KbVersion).where(KbVersion.status == "active")
    if subject is not None:
        stmt = stmt.where(KbVersion.subject == subject)
    if grade is not None:
        exact = stmt.where(KbVersion.grade == grade).order_by(KbVersion.id.desc())
        kb = session.scalar(exact)
        if kb is None:
            # 通用版本可承载多个年级，是精确版本缺失时的安全回落。
            kb = session.scalar(stmt.where(KbVersion.grade.is_(None)).order_by(KbVersion.id.desc()))
    else:
        kb = session.scalar(stmt.order_by(KbVersion.id.desc()))
    if kb is not None:
        return kb
    if strict_active():
        scope = []
        if subject:
            scope.append(f"学科：{subject}")
        if grade is not None:
            scope.append(f"年级：{grade}")
        scope_text = f"（{'，'.join(scope)}）" if scope else ""
        raise KbNotActiveError(
            f"无审核通过(active)的知识库版本{scope_text}，请先审核并激活"
            "（SC_KB_STRICT_ACTIVE 已开启）"
        )
    stmt2 = select(KbVersion)
    if subject is not None:
        stmt2 = stmt2.where(KbVersion.subject == subject)
    if grade is not None:
        exact = stmt2.where(KbVersion.grade == grade).order_by(KbVersion.id.desc())
        kb = session.scalar(exact)
        if kb is None:
            kb = session.scalar(stmt2.where(KbVersion.grade.is_(None)).order_by(KbVersion.id.desc()))
    else:
        kb = session.scalar(stmt2.order_by(KbVersion.id.desc()))
    if kb is None:
        return None
    if kb.status != "active":
        logger.warning(
            "分析层兜底使用未激活的知识库版本(id=%d, status=%s, %s %s v%s)，"
            "该版本未经教研审核，归因结果需谨慎核对（improvement-plan §2.1）",
            kb.id,
            kb.status,
            kb.subject,
            kb.textbook_edition,
            kb.version,
        )
    return kb
