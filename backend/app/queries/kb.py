"""知识库浏览聚合（架构修复 候选2：列表端点的 N+1 → 批量取）。

一次 group_by 取全部分版本的 kp 数，替代「每个版本一条 count」的 N+1。
纯查询，不感知 HTTP。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import KbVersion, KnowledgePoint


def kb_versions_list(session: Session) -> list[dict]:
    """全部版本的列表（kp_count 一次聚合）。"""
    rows = list(session.scalars(select(KbVersion).order_by(KbVersion.id.desc())))
    counts = dict(
        session.execute(
            select(
                KnowledgePoint.kb_version_id, func.count(KnowledgePoint.id)
            ).group_by(KnowledgePoint.kb_version_id)
        ).all()
    )
    out = []
    for kb in rows:
        out.append(
            {
                "id": kb.id,
                "subject": kb.subject,
                "textbook_edition": kb.textbook_edition,
                "version": kb.version,
                "status": kb.status,
                "created_at": kb.created_at.isoformat() if kb.created_at else None,
                "kp_count": counts.get(kb.id, 0),
                "is_active": kb.status == "active",
            }
        )
    return out