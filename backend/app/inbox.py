"""收件箱与 draft 流（agent-product-design §5.3，Phase 2 批次B）。

报告签发状态机：draft（待签发，收件箱可见）→ issued（教师签发）
或 archived（打回）。存量报告与自动生成的确定性报告默认 issued——
「签发」语义只对 Agent 起草的 draft 有意义。

设计要点：
- 状态迁移集中在 transition() 一处校验，非法迁移抛 ValueError；
- 列表按类型/班级过滤 + 分页（上下文预算军规同工具面）；
- 打回保留原文进 archived，供回溯与再起草对照；
- 本层不感知 HTTP；端点在 api/routers/reports.py。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import Class, Report, Student

# 报告类型 → 收件箱展示名（§4.3 审批收件箱列表项）
TYPE_LABELS = {
    "quality_analysis": "班级质量分析",
    "student_diagnosis": "学生诊断单",
    "class_improvement_advice": "班级改进意见",
    "student_action_plan": "学生改进单",
}

VALID_STATUSES = ("draft", "issued", "archived")

# 合法迁移表：draft → issued/archived；issued/archived 为终态（再起草=新建报告）
_TRANSITIONS = {
    "draft": {"issued", "archived"},
    "issued": set(),
    "archived": set(),
}

MAX_PAGE = 50


def _label(report_type: str) -> str:
    return TYPE_LABELS.get(report_type, report_type)


def _subject_name(session: Session, report: Report) -> str | None:
    """草稿主体名：学生单取别名，班级单取班名（§9 最小化）。"""
    if report.student_id is not None:
        stu = session.get(Student, report.student_id)
        return stu.name_or_alias if stu else None
    if report.class_id is not None:
        clazz = session.get(Class, report.class_id)
        return clazz.name if clazz else None
    return None


def list_drafts(
    session: Session,
    class_id: int | None = None,
    status: str = "draft",
    offset: int = 0,
    limit: int = MAX_PAGE,
) -> dict:
    """收件箱列表：默认待签发草稿，含差异预览（markdown 前 200 字）。"""
    if status not in VALID_STATUSES:
        raise ValueError(f"非法状态 {status!r}，应为 {'/'.join(VALID_STATUSES)}")
    conds = [Report.status == status]
    if class_id is not None:
        conds.append(Report.class_id == class_id)
    total = session.scalar(select(func.count(Report.id)).where(*conds)) or 0
    rows = list(
        session.scalars(
            select(Report)
            .where(*conds)
            .order_by(Report.generated_at.desc(), Report.id.desc())
            .offset(offset)
            .limit(max(1, min(limit, MAX_PAGE)))
        )
    )
    items = []
    for r in rows:
        md = r.content_markdown or ""
        items.append(
            {
                "report_id": r.id,
                "type": r.type,
                "type_label": _label(r.type),
                "class_id": r.class_id,
                "subject": _subject_name(session, r),
                "exam_id": r.exam_id,
                "generated_at": r.generated_at.isoformat(timespec="seconds") if r.generated_at else None,
                "preview": md[:200],
                "chars": len(md),
                "writer": (r.snapshot_json or {}).get("writer"),
            }
        )
    return {
        "status": status,
        "total": total,
        "offset": offset,
        "items": items,
        "has_more": (offset + len(items)) < total,
    }


def get_report_checked(session: Session, report_id: int) -> Report:
    report = session.get(Report, report_id)
    if report is None:
        raise LookupError(f"报告 {report_id} 不存在")
    return report


def transition(
    session: Session,
    report_id: int,
    action: str,
    note: str | None = None,
) -> dict:
    """状态迁移：action ∈ issue（签发）/ reject（打回）/ archive（归档）。

    非法迁移（终态再动、未知 action）抛 ValueError。签发/打回都盖
    status_changed_at 时间戳；打回必须给理由（note），落 status_note。
    """
    report = get_report_checked(session, report_id)
    action_to = {"issue": "issued", "reject": "archived", "archive": "archived"}
    if action not in action_to:
        raise ValueError(f"未知操作 {action!r}，应为 issue/reject/archive")
    target = action_to[action]
    if action == "reject" and not (note or "").strip():
        raise ValueError("打回必须附理由")
    allowed = _TRANSITIONS.get(report.status, set())
    if target not in allowed:
        raise ValueError(f"报告当前状态为 {report.status}，不能执行 {action}")
    report.status = target
    report.status_changed_at = utcnow()
    report.status_note = note.strip() if note and note.strip() else None
    session.flush()
    return {
        "report_id": report.id,
        "status": report.status,
        "status_changed_at": report.status_changed_at.isoformat(timespec="seconds"),
        "status_note": report.status_note,
        "type_label": _label(report.type),
    }


def inbox_summary(session: Session) -> dict:
    """角标计数：各状态报告数（前端导航「待签发」徽标数据源）。"""
    counts = dict(
        session.execute(select(Report.status, func.count(Report.id)).group_by(Report.status)).all()
    )
    return {
        "draft": counts.get("draft", 0),
        "issued": counts.get("issued", 0),
        "archived": counts.get("archived", 0),
    }
