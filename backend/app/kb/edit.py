"""知识库编辑：kp / relation CRUD 校验与级联（架构修复 候选2：从 routes 抽出的领域模块）。

领域层只抛领域异常（``ValueError`` 子类），HTTP 状态码由路由层按异常类型翻译：
- ``KbNotFoundError`` → 404；
- ``KbConfirmRequiredError`` → 409（需显式 confirm 的高风险操作）；
- ``KbEditError``（其余校验失败）→ 400。

写路径语义（kb-edit §4.3/§4.4）：
- 软归档默认，被题目标注的 kp 归档需 ``confirm=True``；
- 硬删（force=True）仅当无证据、无题目标注；归档即清教学进度残留；
- 所有变更写 ``CorrectionLog`` 留痕（教师改动来源=teacher）。
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.kb.floor_impact import floor_impact, weak_count_for_kp
from app.models import (
    CorrectionLog,
    EvidenceEvent,
    KnowledgePoint,
    KpRelation,
    QuestionKp,
    TeachingProgress,
)

RELATION_TYPES = ("prerequisite", "contains", "confusable", "spiral")
COG_LEVELS = ("识记", "理解", "应用", "综合")
_IMPORTANCE_LEVELS = ("基础", "核心", "拓展")
_UPDATE_FIELDS = (
    "name",
    "description",
    "chapter",
    "semester",
    "cog_levels_expected",
    "difficulty_prior",
    "mastery_floor",
    "importance",
    "archived",
)


class KbEditError(ValueError):
    """kb 编辑校验失败（拒绝性错误 → HTTP 400）。"""


class KbNotFoundError(KbEditError):
    """目标对象不存在 → HTTP 404。"""


class KbConfirmRequiredError(KbEditError):
    """需显式 confirm 的高风险操作 → HTTP 409。"""


def log_correction(
    session: Session,
    entity_type: str,
    entity_id: int,
    field: str,
    old,  # noqa: ANN001
    new,  # noqa: ANN001
    by: str,
) -> None:
    """教师改动留痕（CorrectionLog）。跨 kb/模板题/作答/归因共用的审计写入。"""
    session.add(
        CorrectionLog(
            entity_type=entity_type,
            entity_id=entity_id,
            field=field,
            old=str(old),
            new=str(new),
            corrected_by=by,
        )
    )


def _validate_cog(cog_levels: list[str]) -> None:
    for level in cog_levels:
        if level not in COG_LEVELS:
            raise KbEditError(f"非法认知层级: {level}")


def create_kp(
    session: Session,
    *,
    kb_version_id: int,
    code: str,
    name: str,
    grade: str,
    chapter: str,
    semester: int,
    description: str | None,
    cog_levels_expected: list[str],
    difficulty_prior: float,
    mastery_floor: float,
    importance: str,
) -> KnowledgePoint:
    """新建知识点（属指定 kb 版本）。code 同版本唯一（uq_kb_code + IntegrityError 兜底）。"""
    _validate_cog(cog_levels_expected)
    if code.startswith("C"):
        raise KbEditError("C 前缀保留给容器节点，新建知识点不可使用")
    if importance not in _IMPORTANCE_LEVELS:
        raise KbEditError(f"非法重要度: {importance}（基础/核心/拓展）")
    kp = KnowledgePoint(
        kb_version_id=kb_version_id,
        code=code,
        name=name,
        grade=grade,
        chapter=chapter,
        semester=semester,
        description=description,
        cog_levels_expected=cog_levels_expected,
        difficulty_prior=difficulty_prior,
        mastery_floor=mastery_floor,
        importance=importance,
    )
    nested = session.begin_nested()
    session.add(kp)
    try:
        session.flush()
    except IntegrityError:
        nested.rollback()
        raise KbEditError(f"知识点编码 {code} 在当前版本已存在") from None
    return kp


def update_kp(
    session: Session,
    kp_id: int,
    *,
    by: str,
    preview: bool = False,
    **fields,  # noqa: ANN003
) -> tuple[KnowledgePoint, dict | None, bool]:
    """改属性（不允许改 code）。改 mastery_floor/difficulty_prior 可 preview 影响。

    返回 ``(kp, impact, previewed)``：
    - ``previewed=True``：走 preview 分支，未落库；impact 为 ``{current, projected, delta}``
      （difficulty 单独变更时附 note）；
    - ``previewed=False``：已落库；impact 为 ``{weak_count, floor}`` 或 None（无杠杆字段）。
    """
    kp = session.get(KnowledgePoint, kp_id)
    if kp is None:
        raise KbNotFoundError("知识点不存在")

    changes: dict[str, tuple] = {}
    for f in _UPDATE_FIELDS:
        val = fields.get(f)
        if val is not None:
            changes[f] = (getattr(kp, f), val)
    if not changes:
        raise KbEditError("未提供任何修改字段")
    if "cog_levels_expected" in changes:
        _validate_cog(changes["cog_levels_expected"][1])
    if "importance" in changes and changes["importance"][1] not in _IMPORTANCE_LEVELS:
        raise KbEditError(
            f"非法重要度: {changes['importance'][1]}（基础/核心/拓展）"
        )

    new_floor = (
        changes["mastery_floor"][1] if "mastery_floor" in changes else kp.mastery_floor
    )
    hi_lever = "mastery_floor" in changes or "difficulty_prior" in changes

    if preview and hi_lever:
        impact = floor_impact(session, kp_id, kp.mastery_floor, new_floor)
        if "difficulty_prior" in changes and "mastery_floor" not in changes:
            impact["note"] = "difficulty_prior 当前未参与掌握度计算，无即时影响"
        return kp, impact, True

    # 落库 + 留痕 CorrectionLog（不变量③：教师改动可追溯）
    for f, (old, new) in changes.items():
        setattr(kp, f, new)
        log_correction(session, "knowledge_point", kp_id, f, old, new, by)
    session.flush()

    impact = None
    if hi_lever:
        impact = {
            "weak_count": weak_count_for_kp(session, kp_id, new_floor),
            "floor": round(new_floor, 4),
        }
    return kp, impact, False


def delete_kp(
    session: Session, kp_id: int, *, force: bool = False, confirm: bool = False
) -> dict:
    """软归档（默认）/ 硬删（force=True）。引用预检见 kb-edit §5。

    返回端点响应内容：
    - 硬删：``{deleted, hard, kp_id}``；
    - 软归档：``{archived, evidence_refs, question_refs, progress_refs, progress_cleared}``。
    """
    kp = session.get(KnowledgePoint, kp_id)
    if kp is None:
        raise KbNotFoundError("知识点不存在")
    if kp.code.startswith("C"):
        raise KbEditError("容器节点不可删除/归档，仅可改名")

    evidence_refs = (
        session.scalar(
            select(func.count(EvidenceEvent.id)).where(EvidenceEvent.kp_id == kp_id)
        )
        or 0
    )
    question_refs = (
        session.scalar(
            select(func.count(QuestionKp.id)).where(QuestionKp.kp_id == kp_id)
        )
        or 0
    )
    progress_refs = (
        session.scalar(
            select(func.count(TeachingProgress.id)).where(
                TeachingProgress.kp_id == kp_id
            )
        )
        or 0
    )

    if force:
        if evidence_refs > 0 or question_refs > 0:
            raise KbEditError(
                f"该知识点被 {evidence_refs} 条证据、{question_refs} 道题标注引用，不可硬删"
            )
        session.execute(
            delete(KpRelation).where(
                (KpRelation.from_kp_id == kp_id) | (KpRelation.to_kp_id == kp_id)
            )
        )
        session.execute(delete(TeachingProgress).where(TeachingProgress.kp_id == kp_id))
        session.delete(kp)
        session.flush()
        return {"deleted": True, "hard": True, "kp_id": kp_id}

    # 软归档：〔v0.2〕被题目标注的 kp 归档需 confirm（防题目标注静默失效）
    if question_refs > 0 and not confirm:
        raise KbConfirmRequiredError(
            f"该知识点被 {question_refs} 道题标注，归档后这些题目的知识点分析将缺失。"
            f"确认归档请带 confirm=true"
        )
    # 〔v0.2〕归档即清教学进度残留（§5.4）
    if progress_refs > 0:
        session.execute(delete(TeachingProgress).where(TeachingProgress.kp_id == kp_id))
    kp.archived = True
    log_correction(session, "knowledge_point", kp_id, "archived", False, True, "teacher")
    session.flush()
    return {
        "archived": True,
        "evidence_refs": evidence_refs,
        "question_refs": question_refs,
        "progress_refs": progress_refs,
        "progress_cleared": progress_refs,
    }


def create_relation(
    session: Session,
    *,
    kb_version_id: int,
    from_kp_id: int,
    to_kp_id: int,
    type: str,  # noqa: A002 —— kb-edit §4.4 关系类型
    weight: float,
) -> KpRelation:
    """新建关系：校验 type/weight/同版本/非自环（kb-edit §4.4/§6.3）。"""
    if type not in RELATION_TYPES:
        raise KbEditError(f"非法关系类型: {type}")
    if not 0.0 <= weight <= 1.0:
        raise KbEditError("关系权重须在 [0,1]")
    if from_kp_id == to_kp_id:
        raise KbEditError("关系端点不可相同（自环）")
    version_kp_ids = set(
        session.scalars(
            select(KnowledgePoint.id).where(KnowledgePoint.kb_version_id == kb_version_id)
        )
    )
    if from_kp_id not in version_kp_ids or to_kp_id not in version_kp_ids:
        raise KbEditError("关系端点不属于当前 active 版本")
    rel = KpRelation(
        from_kp_id=from_kp_id, to_kp_id=to_kp_id, type=type, weight=weight
    )
    session.add(rel)
    session.flush()
    return rel


def update_relation(
    session: Session, rel_id: int, *, type: str | None = None, weight: float | None = None  # noqa: A002
) -> KpRelation:
    rel = session.get(KpRelation, rel_id)
    if rel is None:
        raise KbNotFoundError("关系不存在")
    if type is not None:
        if type not in RELATION_TYPES:
            raise KbEditError(f"非法关系类型: {type}")
        rel.type = type
    if weight is not None:
        if not 0.0 <= weight <= 1.0:
            raise KbEditError("关系权重须在 [0,1]")
        rel.weight = weight
    session.flush()
    return rel


def delete_relation(session: Session, rel_id: int) -> None:
    rel = session.get(KpRelation, rel_id)
    if rel is None:
        raise KbNotFoundError("关系不存在")
    session.delete(rel)
    session.flush()