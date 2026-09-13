"""Durable, database-backed background jobs.

The queue is intentionally small: it provides a reliable hand-off for expensive
post-commit work while preserving the existing synchronous path by default. Set
``SC_JOB_QUEUE_ENABLE=1`` in production to let requests enqueue work and let one
or more backend replicas claim rows from the shared database.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db import SessionLocal, utcnow
from app.metrics import inc
from app.models import AsyncJob

logger = logging.getLogger(__name__)

JobHandler = Callable[[dict[str, Any]], dict[str, Any] | None]
_HANDLERS: dict[str, JobHandler] = {}
_WORKER_LOCK = threading.Lock()


def enabled() -> bool:
    return (os.environ.get("SC_JOB_QUEUE_ENABLE") or "").lower() in {"1", "true", "yes"}


def register(kind: str, handler: JobHandler) -> None:
    _HANDLERS[kind] = handler


def enqueue(
    session: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    idempotency_key: str | None = None,
    max_attempts: int = 3,
) -> AsyncJob:
    """Insert or return an existing job for a stable idempotency key."""
    if idempotency_key:
        existing = session.scalar(
            select(AsyncJob).where(
                AsyncJob.kind == kind, AsyncJob.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            return existing
    job = AsyncJob(
        kind=kind,
        idempotency_key=idempotency_key,
        payload_json=payload,
        max_attempts=max(1, max_attempts),
        status="queued",
    )
    session.add(job)
    session.flush()
    return job


def enqueue_exam_reports(session: Session, exam_id: int) -> AsyncJob:
    return enqueue(
        session,
        "exam_reports",
        {"exam_id": exam_id},
        idempotency_key=f"exam_reports:{exam_id}",
    )


def get(session: Session, job_id: int) -> AsyncJob | None:
    return session.get(AsyncJob, job_id)


def _claim(session: Session, worker_id: str) -> AsyncJob | None:
    now = utcnow()
    stale_before = now - timedelta(minutes=10)
    terminal_stale = session.scalars(
        select(AsyncJob).where(
            AsyncJob.status == "running",
            AsyncJob.locked_at.is_not(None),
            AsyncJob.locked_at < stale_before,
            AsyncJob.attempts >= AsyncJob.max_attempts,
        )
    ).all()
    for stale in terminal_stale:
        stale.status = "failed"
        stale.error = "worker lease expired after maximum attempts"
        stale.finished_at = now
        stale.locked_at = None
        stale.locked_by = None
    session.flush()
    candidate = session.scalar(
        select(AsyncJob)
        .where(
            or_(
                and_(AsyncJob.status == "queued", AsyncJob.available_at <= now),
                and_(
                    AsyncJob.status == "running",
                    AsyncJob.locked_at.is_not(None),
                    AsyncJob.locked_at < stale_before,
                    AsyncJob.attempts < AsyncJob.max_attempts,
                ),
            )
        )
        .order_by(AsyncJob.available_at, AsyncJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if candidate is None:
        return None
    # A stale worker lease is reclaimed; an atomic row lock on PostgreSQL keeps
    # two replicas from claiming the same job. SQLite remains best-effort under
    # its single-writer constraint.
    candidate.status = "running"
    candidate.locked_by = worker_id
    candidate.locked_at = now
    candidate.attempts += 1
    session.flush()
    return candidate


def _complete(session: Session, job_id: int, result: dict[str, Any] | None) -> None:
    job = session.get(AsyncJob, job_id)
    if job is None:
        return
    job.status = "succeeded"
    job.result_json = result or {}
    job.error = None
    job.finished_at = utcnow()
    job.locked_at = None
    job.locked_by = None


def _fail(session: Session, job_id: int, error: str) -> None:
    job = session.get(AsyncJob, job_id)
    if job is None:
        return
    job.error = error[:2000]
    job.locked_at = None
    job.locked_by = None
    if job.attempts < job.max_attempts:
        job.status = "queued"
        job.available_at = utcnow() + timedelta(seconds=min(60, 2**job.attempts))
    else:
        job.status = "failed"
        job.finished_at = utcnow()


def _register_builtin_handlers() -> None:
    if "exam_reports" in _HANDLERS:
        return

    def run_exam_reports(payload: dict[str, Any]) -> dict[str, Any]:
        exam_id = int(payload["exam_id"])
        from app.reports.auto_generate import generate_exam_reports
        from app.triggers import fire_post_exam_analysis

        with SessionLocal() as session:
            result = generate_exam_reports(session, exam_id)
            session.commit()
            # Triggering after the report transaction commits keeps the durable
            # report and the agent task causally ordered.
            fire_post_exam_analysis(session, exam_id)
            return {
                "quality_report": result.quality,
                "diagnoses": result.diagnoses,
                "action_plans": result.action_plans,
                "interventions": result.interventions,
            }

    register("exam_reports", run_exam_reports)

    def run_photo_template(payload: dict[str, Any]) -> dict[str, Any]:
        from datetime import date

        from app.ha import ObjectStore
        from app.ingestion.photo import parse_template_from_photo

        with SessionLocal() as session:
            result = parse_template_from_photo(
                session,
                int(payload["kb_id"]),
                int(payload["class_id"]),
                str(payload["name"]),
                date.fromisoformat(str(payload["exam_date"])),
                str(payload["type"]),
                ObjectStore().get(str(payload["object_key"])),
            )
            session.commit()
            return {
                "exam_id": result.exam_id,
                "parse_job_id": result.parse_job_id,
                "questions": result.questions,
                "warnings": result.warnings,
            }

    def run_photo_response(payload: dict[str, Any]) -> dict[str, Any]:
        from app.ha import ObjectStore
        from app.ingestion.photo import parse_student_response_from_photo

        with SessionLocal() as session:
            result = parse_student_response_from_photo(
                session,
                int(payload["exam_id"]),
                int(payload["student_id"]),
                ObjectStore().get(str(payload["object_key"])),
            )
            session.commit()
            return {
                "response_id": result.response_id,
                "parse_job_id": result.parse_job_id,
                "warnings": result.warnings,
            }

    register("photo_template", run_photo_template)
    register("photo_response", run_photo_response)


def run_once(*, worker_id: str | None = None) -> bool:
    """Claim and execute one job. Returns False when no work is available."""
    _register_builtin_handlers()
    worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
    with _WORKER_LOCK:
        with SessionLocal() as session:
            job = _claim(session, worker_id)
            if job is None:
                session.commit()
                return False
            job_id = job.id
            kind = job.kind
            payload = dict(job.payload_json or {})
            session.commit()
        inc("jobs_claimed_total")
        handler = _HANDLERS.get(kind)
        if handler is None:
            error = f"unknown job kind: {kind}"
            with SessionLocal() as session:
                _fail(session, job_id, error)
                session.commit()
            inc("jobs_failed_total")
            return True
        try:
            result = handler(payload)
        except Exception as exc:  # noqa: BLE001 - persisted for operator visibility
            logger.exception("async job %s failed (attempt=%s)", job_id, kind)
            with SessionLocal() as session:
                _fail(session, job_id, f"{type(exc).__name__}: {exc}")
                session.commit()
            inc("jobs_failed_total")
        else:
            with SessionLocal() as session:
                _complete(session, job_id, result)
                session.commit()
            inc("jobs_succeeded_total")
        return True


async def worker_loop(stop: asyncio.Event) -> None:
    """Run a cooperative worker in the backend process when queue mode is on."""
    while not stop.is_set():
        did_work = await asyncio.to_thread(run_once)
        if not did_work:
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
