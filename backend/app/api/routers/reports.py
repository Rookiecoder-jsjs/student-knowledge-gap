"""报告回读路由 + 收件箱 draft 流端点（候选2 拆分；领域逻辑在 app.inbox）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import _auth as auth_mod
from app.api.deps import get_db, guard_class, require_teacher
from app import inbox
from app.models import Class as ClassModel
from app.models import Report, Student

router = APIRouter()


class ReportActionRequest(BaseModel):
    note: str | None = None


@router.get("/reports")
def list_reports(
    class_id: int | None = None,
    student_id: int | None = None,
    exam_id: int | None = None,
    ctx=Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """报告列表。显式 class_id 做归属校验；未传时安全模式收敛到授权班级。"""
    if class_id is not None:
        if db.get(ClassModel, class_id) is None:
            # 不存在的班级过滤=空集（不泄露存在性；与既有行为一致）
            return {"reports": []}
        guard_class(class_id, db, ctx)
    if student_id is not None:
        stu = db.get(Student, student_id)
        if stu is None:
            raise HTTPException(404, "学生不存在")
        guard_class(stu.class_id, db, ctx)
    stmt = select(Report).order_by(Report.generated_at.desc(), Report.id.desc())
    if class_id is not None:
        stmt = stmt.where(Report.class_id == class_id)
    else:
        allowed = auth_mod.allowed_class_ids(db, ctx)
        if allowed is not None:
            stmt = stmt.where(Report.class_id.in_(allowed or [-1]))
    if student_id is not None:
        stmt = stmt.where(Report.student_id == student_id)
    if exam_id is not None:
        stmt = stmt.where(Report.exam_id == exam_id)
    return {
        "reports": [
            {
                "report_id": r.id,
                "type": r.type,
                "class_id": r.class_id,
                "student_id": r.student_id,
                "exam_id": r.exam_id,
                "generated_at": r.generated_at.isoformat() if r.generated_at else None,
            }
            for r in db.scalars(stmt)
        ]
    }


@router.get("/reports/{report_id}")
def report_detail(report_id: int, ctx=Depends(require_teacher), db: Session = Depends(get_db)):
    report = _report_or_404(db, report_id)
    _guard_report(db, ctx, report)
    return {
        "report_id": report.id,
        "type": report.type,
        "class_id": report.class_id,
        "student_id": report.student_id,
        "exam_id": report.exam_id,
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        "status": report.status,
        "markdown": report.content_markdown,
        "snapshot": report.snapshot_json,
    }


# ---------------------------------------------------------------------------
# 收件箱与 draft 流（§5.3）：列表 / 详情 / 签发 / 打回 / 角标
# ---------------------------------------------------------------------------


@router.get("/inbox")
def inbox_list(
    class_id: int | None = None,
    status: str = "draft",
    offset: int = 0,
    limit: int = 50,
    ctx=Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """审批收件箱：默认待签发草稿（类型/班级/时间/预览），分页。

    安全模式未传 class_id 时按授权班级收敛。
    """
    if class_id is not None:
        guard_class(class_id, db, ctx)
    else:
        allowed = auth_mod.allowed_class_ids(db, ctx)
        if allowed is not None and len(allowed) == 1:
            class_id = allowed[0]
    try:
        data = inbox.list_drafts(db, class_id=class_id, status=status,
                                 offset=offset, limit=limit)
    except ValueError as e:
        raise HTTPException(400, str(e))
    allowed = auth_mod.allowed_class_ids(db, ctx)
    if allowed is not None and class_id is None:
        want = set(allowed)
        data["items"] = [i for i in data["items"] if i.get("class_id") in want]
        data["total"] = len(data["items"])
    return data


@router.get("/inbox/summary")
def inbox_counts(db: Session = Depends(get_db)):
    """角标计数：各状态报告数（导航「待签发」徽标）。"""
    return inbox.inbox_summary(db)


@router.get("/reports/{report_id}/full")
def report_full(report_id: int, ctx=Depends(require_teacher), db: Session = Depends(get_db)):
    """收件箱签发前的全文视图（markdown 全文 + 状态轨迹）。"""
    try:
        r = inbox.get_report_checked(db, report_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    _guard_report(db, ctx, r)
    return {
        "report_id": r.id,
        "type": r.type,
        "type_label": inbox.TYPE_LABELS.get(r.type, r.type),
        "class_id": r.class_id,
        "student_id": r.student_id,
        "exam_id": r.exam_id,
        "generated_at": r.generated_at.isoformat() if r.generated_at else None,
        "status": r.status,
        "status_note": r.status_note,
        "markdown": r.content_markdown,
        "snapshot": r.snapshot_json,
    }


@router.post("/reports/{report_id}/issue")
def issue_report(
    report_id: int, req: ReportActionRequest | None = None, ctx=Depends(require_teacher), db: Session = Depends(get_db)
):
    """签发草稿 → issued（终态；再起草走新建报告）。"""
    return _do_action(report_id, "issue", req, ctx, db)


@router.post("/reports/{report_id}/reject")
def reject_report(
    report_id: int, req: ReportActionRequest, ctx=Depends(require_teacher), db: Session = Depends(get_db)
):
    """打回草稿 → archived（必须附理由，保留原文供回溯）。"""
    return _do_action(report_id, "reject", req, ctx, db)


def _do_action(report_id: int, action: str, req: ReportActionRequest | None, ctx, db: Session):
    try:
        r = inbox.get_report_checked(db, report_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    _guard_report(db, ctx, r)
    try:
        return inbox.transition(db, report_id, action, note=req.note if req else None)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))

def _report_or_404(db: Session, report_id: int) -> Report:
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
    return report


def _guard_report(db: Session, ctx, report: Report) -> None:
    """报告归属校验：班级单看 class_id，学生单经学生解析到班级。"""
    if report.student_id is not None:
        stu = db.get(Student, report.student_id)
        if stu is not None:
            guard_class(stu.class_id, db, ctx)
    elif report.class_id is not None:
        guard_class(report.class_id, db, ctx)
