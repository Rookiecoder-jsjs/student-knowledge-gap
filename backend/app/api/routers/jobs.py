"""Scoped durable job status and explicit retry."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.deps import get_db, guard_class, guard_exam, require_teacher
from app.db import utcnow
from app.models import AsyncJob, Class, ExamTemplate, KbVersion

router = APIRouter(prefix="/jobs", tags=["jobs"])


class QueuedJob(BaseModel):
    job_id: int
    status: Literal["queued"] = "queued"
    next: str


class PhotoTemplateResult(BaseModel):
    exam_id: int
    questions: int
    warnings: list[str]
    parse_job_id: int | None = None
    next: str | None = None


class PhotoResponseResult(BaseModel):
    response_id: int
    warnings: list[str]
    parse_job_id: int | None = None
    next: str | None = None


class JobStatus(BaseModel):
    id: int
    kind: str
    status: Literal["queued", "running", "succeeded", "failed"]
    attempts: int
    max_attempts: int
    result: dict | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


def _scoped_job(db: Session, job_id: int, ctx) -> AsyncJob:
    job = db.get(AsyncJob, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    payload = job.payload_json or {}
    if job.kind == "photo_template":
        class_id = payload.get("class_id")
    elif job.kind in {"photo_response", "exam_reports"}:
        exam = db.get(ExamTemplate, payload.get("exam_id")) if payload.get("exam_id") else None
        class_id = exam.class_id if exam else None
        if exam is not None:
            guard_exam(exam, db, ctx)
    else:
        class_id = None
    if not isinstance(class_id, int) or class_id <= 0 or db.get(Class, class_id) is None:
        # Unknown/legacy jobs fail closed, including for open-mode callers.
        raise HTTPException(404, "任务不存在或关联数据已删除")
    guard_class(class_id, db, ctx)
    if job.kind == "photo_template":
        kb = db.get(KbVersion, payload.get("kb_id")) if payload.get("kb_id") else None
        if kb is None:
            raise HTTPException(404, "任务关联知识库已删除")
        # Before the exam exists, the selected knowledge base defines its subject.
        guard_exam(ExamTemplate(class_id=class_id, subject=kb.subject), db, ctx)
    return job


def _view(job: AsyncJob) -> dict:
    return {
        "id": job.id, "kind": job.kind, "status": job.status,
        "attempts": job.attempts, "max_attempts": job.max_attempts,
        "result": job.result_json if job.status == "succeeded" else None,
        # Provider/DB exceptions can contain credentials or user content.
        "error": "任务处理失败，请重试；若持续失败，请联系管理员。" if job.error else None,
        "created_at": job.created_at, "finished_at": job.finished_at,
    }


@router.get("/{job_id}", response_model=JobStatus)
def job_status(job_id: int, ctx=Depends(require_teacher), db: Session = Depends(get_db)):
    return _view(_scoped_job(db, job_id, ctx))


@router.post("/{job_id}/retry", response_model=JobStatus)
def retry_job(job_id: int, ctx=Depends(require_teacher), db: Session = Depends(get_db)):
    job = _scoped_job(db, job_id, ctx)
    changed = db.execute(update(AsyncJob).where(
        AsyncJob.id == job.id, AsyncJob.status == "failed",
    ).values(status="queued", available_at=utcnow(), error=None, result_json=None,
             finished_at=None, locked_at=None, locked_by=None,
             max_attempts=AsyncJob.attempts + 3))
    if changed.rowcount != 1:
        raise HTTPException(409, "只有失败的任务可以重试")
    db.flush()
    db.refresh(job)
    return _view(job)
