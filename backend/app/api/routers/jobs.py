"""Read-only status endpoint for durable background jobs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_teacher
from app.models import AsyncJob

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
def job_status(job_id: int, _ctx=Depends(require_teacher), db: Session = Depends(get_db)):
    job = db.get(AsyncJob, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "result": job.result_json,
        "error": job.error,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }
