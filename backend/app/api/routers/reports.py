"""报告回读路由：列表与详情（候选2 拆分；无领域逻辑，直接查询）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Report

router = APIRouter()


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
        "markdown": report.content_markdown,
        "snapshot": report.snapshot_json,
    }