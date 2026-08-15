"""知识库版本管理：fork / 切 active / 导出（架构修复 候选2：从 routes 抽出版本域逻辑）。

切换语义（kb-edit §4.5/§6.1-§6.5）：
- fork 复制 kp（含 archived）+ 关系为 draft 新版本；
- 切 active 做 code 超集 + 高杠杆属性校验：缺失 code 需 ``force``（接受旧证据失联），
  敏感属性变化需 ``confirm``；旧 active 降 reviewed（不删，可结构回滚切回），写切换日志；
- 导出最终 YAML 与 loader 格式对齐（可读回）。

领域异常（kb.edit）由路由层翻译 HTTP 状态码。
"""

from __future__ import annotations

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kb.compatibility import compatibility
from app.kb.edit import KbConfirmRequiredError, KbEditError, log_correction
from app.models import KbVersion, KnowledgePoint, KpRelation

_ACTIVATION_NOTE = "切换后新产生的考试证据无法迁回旧版本"


def fork_kb_version(session: Session, src: KbVersion) -> KbVersion:
    """fork 当前 active：复制其 kp（含 archived）+ 关系为草稿新版本（kb-edit §4.5/§6.3）。"""
    new = KbVersion(
        subject=src.subject,
        textbook_edition=src.textbook_edition,
        version=f"{src.version}-fork",
        status="draft",
    )
    session.add(new)
    session.flush()
    # 复制 kp（含 archived），建立 src_id -> new_id 映射
    id_map: dict[int, int] = {}
    for kp in session.scalars(
        select(KnowledgePoint).where(KnowledgePoint.kb_version_id == src.id)
    ):
        nk = KnowledgePoint(
            kb_version_id=new.id,
            code=kp.code,
            name=kp.name,
            description=kp.description,
            grade=kp.grade,
            semester=kp.semester,
            chapter=kp.chapter,
            cog_levels_expected=kp.cog_levels_expected,
            difficulty_prior=kp.difficulty_prior,
            mastery_floor=kp.mastery_floor,
            archived=kp.archived,
        )
        session.add(nk)
        session.flush()
        id_map[kp.id] = nk.id
    # 复制关系：端点都在 src 版本内的，按 id_map 映射到新版本（§6.3）
    src_ids = set(id_map)
    for rel in session.scalars(select(KpRelation).order_by(KpRelation.id)):
        if rel.from_kp_id in src_ids and rel.to_kp_id in src_ids:
            session.add(
                KpRelation(
                    from_kp_id=id_map[rel.from_kp_id],
                    to_kp_id=id_map[rel.to_kp_id],
                    type=rel.type,
                    weight=rel.weight,
                    audit_status="draft",
                )
            )
    session.flush()
    return new


def activate_kb_version(
    session: Session,
    target: KbVersion,
    active: KbVersion,
    *,
    force: bool = False,
    confirm: bool = False,
    by: str = "teacher",
) -> dict:
    """把 target 切为 active（旧 active 降 reviewed）。

    返回端点响应内容（{id, status, switched_from, missing_codes_accepted,
    attribute_changes_accepted, note}）。
    """
    if target.status == "active":
        raise KbEditError("该版本已是 active")
    comp = compatibility(session, active, target)
    # ① code 超集：缺失 code 需 force（接受旧证据失联）
    missing = comp["missing_codes"]
    if missing and not force:
        raise KbEditError(
            f"目标版本缺失 code: {missing}（旧证据会从分析消失）。确认丢失请带 force=true"
        )
    # ② 高杠杆参数变化需 confirm（分析结论会改变）
    attr = comp["attribute_changes"]
    if attr and not confirm:
        raise KbConfirmRequiredError(
            f"切换将改变 {len(attr)} 个知识点的高杠杆参数，分析结论会改变。确认请带 confirm=true"
        )
    # 切换：旧 active 降 reviewed（不删，可结构回滚切回），目标置 active，写切换日志
    from_id = active.id
    active.status = "reviewed"
    target.status = "active"
    log_correction(session, "kb_version", target.id, "active", from_id, target.id, by)
    session.flush()
    return {
        "id": target.id,
        "status": "active",
        "switched_from": from_id,
        "missing_codes_accepted": missing if missing and force else [],
        "attribute_changes_accepted": attr if attr and confirm else [],
        "note": _ACTIVATION_NOTE,
    }


def export_kb_yaml(session: Session, kb: KbVersion) -> str:
    """从 DB 现状生成 YAML（对齐 loader 格式，可读回，kb-edit §4.6）。"""
    kps = list(
        session.scalars(
            select(KnowledgePoint)
            .where(KnowledgePoint.kb_version_id == kb.id)
            .order_by(KnowledgePoint.code)
        )
    )
    id_to_code = {kp.id: kp.code for kp in kps}
    version_ids = set(id_to_code)
    relations = []
    for rel in session.scalars(select(KpRelation).order_by(KpRelation.id)):
        if rel.from_kp_id in version_ids and rel.to_kp_id in version_ids:
            relations.append(
                {
                    "from": id_to_code[rel.from_kp_id],
                    "to": id_to_code[rel.to_kp_id],
                    "type": rel.type,
                    "weight": rel.weight,
                }
            )
    points = []
    for kp in kps:
        item = {
            "code": kp.code,
            "name": kp.name,
            "description": kp.description,
            "grade": kp.grade,
            "semester": kp.semester,
            "chapter": kp.chapter,
            "cog_levels_expected": kp.cog_levels_expected,
            "difficulty_prior": kp.difficulty_prior,
            "mastery_floor": kp.mastery_floor,
            "is_container": kp.code.startswith("C"),
        }
        if kp.archived:
            item["archived"] = True
        points.append(item)
    data = {
        "meta": {
            "subject": kb.subject,
            "textbook_edition": kb.textbook_edition,
            "version": kb.version,
            "status": kb.status,
        },
        "knowledge_points": points,
        "relations": relations,
    }
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)