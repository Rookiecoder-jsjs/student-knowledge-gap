"""第二批：教师今日队列、补证据与归因验证预览/建卷。"""
from datetime import date, datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import auth
from app.api.deps import _active_kb, _graph, get_db, guard_class, require_teacher
from app.diagnostic_tasks import evidence_gaps, diagnostic_blueprint, create_diagnostic_exam
from app.models import Class, Student
from app.queries.teacher_workbench import teacher_workbench

router = APIRouter()


def _context(db, ctx, class_id):
    clazz = db.get(Class, class_id)
    if clazz is None:
        raise HTTPException(404, "班级不存在")
    guard_class(class_id, db, ctx)
    subject = auth.class_subject(db, ctx, clazz)
    kb = _active_kb(db, subject, clazz.grade)
    return clazz, subject, _graph(db, kb.id)


def _student_context(db, ctx, student_id):
    stu = db.get(Student, student_id)
    if stu is None:
        raise HTTPException(404, "学生不存在")
    return stu, _context(db, ctx, stu.class_id)[2]


@router.get("/classes/{class_id}/today-tasks")
def today_tasks(class_id: int, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50),
                ctx=Depends(require_teacher), db: Session = Depends(get_db)):
    clazz, subject, graph = _context(db, ctx, class_id)
    return teacher_workbench(db, clazz.id, subject, graph.kp_ids(), date.today(), offset, limit)


@router.get("/students/{student_id}/evidence-gaps")
def get_gaps(student_id: int, ctx=Depends(require_teacher), db: Session = Depends(get_db)):
    stu, graph = _student_context(db, ctx, student_id)
    return evidence_gaps(db, graph, stu, datetime.now())


@router.get("/students/{student_id}/diagnostic-blueprint")
def blueprint(student_id: int, kp_id: int, mode: Literal["evidence", "attribution"] = "evidence",
              attribution_id: int | None = None, ctx=Depends(require_teacher), db: Session = Depends(get_db)):
    stu, graph = _student_context(db, ctx, student_id)
    try:
        return diagnostic_blueprint(db, graph, stu, kp_id, mode, attribution_id, datetime.now())
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class DiagnosticRequest(BaseModel):
    kp_id: int
    mode: Literal["evidence", "attribution"]
    attribution_id: int | None = None
    question_ids: list[int] = Field(min_length=1, max_length=3)
    exam_date: date
    preview_token: str = Field(min_length=64, max_length=64)


@router.post("/students/{student_id}/diagnostic-exams")
def create_exam(student_id: int, req: DiagnosticRequest, ctx=Depends(require_teacher), db: Session = Depends(get_db)):
    stu, graph = _student_context(db, ctx, student_id)
    try:
        plan = diagnostic_blueprint(db, graph, stu, req.kp_id, req.mode, req.attribution_id, datetime.now())
        exam = create_diagnostic_exam(db, stu, plan, req.question_ids, req.exam_date, req.preview_token)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    return {"exam_id": exam.id, "class_id": exam.class_id, "name": exam.name, "student_id": stu.id,
            "question_count": len(plan["slots"])}
