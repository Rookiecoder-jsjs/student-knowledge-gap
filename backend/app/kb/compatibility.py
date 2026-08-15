"""版本兼容性 diff（架构修复 候选2：从 routes 抽出的 kb 切换预览逻辑）。

目标版本 vs 当前 active：code 差集 + 高杠杆属性 diff（kb-edit §6.1/§6.2/§6.5）。
切换前预览用；missing_codes 意味着旧证据会从分析消失。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KbVersion, KnowledgePoint

# 切换会改变分析结论的高杠杆字段（§6.2）
_SENSITIVE_FIELDS = ("mastery_floor", "difficulty_prior", "archived")


def _kps_by_code(session: Session, kb: KbVersion) -> dict[str, KnowledgePoint]:
    return {
        k.code: k
        for k in session.scalars(
            select(KnowledgePoint).where(KnowledgePoint.kb_version_id == kb.id)
        )
    }


def compatibility(session: Session, active: KbVersion, target: KbVersion) -> dict:
    """active vs target 的差异。

    返回 ``{"missing_codes", "new_codes", "attribute_changes"}``；
    - missing：active 有、target 无 → 切换后旧证据失联；
    - new：target 新增；
    - attribute_changes：[{code, field, old, new}]（仅敏感字段）。
    """
    active_kps = _kps_by_code(session, active)
    target_kps = _kps_by_code(session, target)
    missing = sorted(set(active_kps) - set(target_kps))
    new = sorted(set(target_kps) - set(active_kps))
    attr_changes = []
    for code in set(active_kps) & set(target_kps):
        a, t = active_kps[code], target_kps[code]
        for field in _SENSITIVE_FIELDS:
            av, tv = getattr(a, field), getattr(t, field)
            if av != tv:
                attr_changes.append({"code": code, "field": field, "old": av, "new": tv})
    return {"missing_codes": missing, "new_codes": new, "attribute_changes": attr_changes}