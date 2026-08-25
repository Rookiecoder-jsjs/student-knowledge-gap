"""报告回读路由 + 收件箱 draft 流端点（候选2 拆分；领域逻辑在 app.inbox）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app import inbox
from app.models import Report

router = APIRouter()


class ReportActionRequest(BaseModel):
    note: str | None = None


@router.get("/reports")
def list_reports(
    class_id: int | None = None,
    student_id: int | None = None,
    exam_id: int | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(Report).order_by(Report.generated_at.desc(), Report.id.desc())
    if class_id is not None:
        stmt = stmt.where(Report.class_id == class_id)
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
def report_detail(report_id: int, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
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
    db: Session = Depends(get_db),
):
    """审批收件箱：默认待签发草稿（类型/班级/时间/预览），分页。"""
    try:
        return inbox.list_drafts(db, class_id=class_id, status=status,
                                 offset=offset, limit=limit)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/inbox/summary")
def inbox_counts(db: Session = Depends(get_db)):
    """角标计数：各状态报告数（导航「待签发」徽标）。"""
    return inbox.inbox_summary(db)


@router.get("/reports/{report_id}/full")
def report_full(report_id: int, db: Session = Depends(get_db)):
    """收件箱签发前的全文视图（markdown 全文 + 状态轨迹）。"""
    try:
        r = inbox.get_report_checked(db, report_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
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
def issue_report(report_id: int, req: ReportActionRequest | None = None, db: Session = Depends(get_db)):
    """签发草稿 → issued（终态；再起草走新建报告）。"""
    return _do_action(report_id, "issue", req, db)


@router.post("/reports/{report_id}/reject")
def reject_report(report_id: int, req: ReportActionRequest, db: Session = Depends(get_db)):
    """打回草稿 → archived（必须附理由，保留原文供回溯）。"""
    return _do_action(report_id, "reject", req, db)


def _do_action(report_id: int, action: str, req: ReportActionRequest | None, db: Session):
    try:
        return inbox.transition(db, report_id, action, note=req.note if req else None)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))