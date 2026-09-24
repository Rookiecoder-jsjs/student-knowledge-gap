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
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionLocal, utcnow
from app.metrics import inc
from app.models import AsyncJob

logger = logging.getLogger(__name__)

JobHandler = Callable[[dict[str, Any]], dict[str, Any] | None]
_HANDLERS: dict[str, JobHandler] = {}
_WORKER_LOCK = threading.Lock()
LEASE_SECONDS = 600
HEARTBEAT_SECONDS = 30
_current_claim: ContextVar[tuple[int, str, int] | None] = ContextVar("job_claim", default=None)


class LeaseLost(RuntimeError):
    """A newer attempt owns the job; this transaction must not commit."""


def _ownership(job_id: int, worker_id: str, attempt: int, *, require_live_lease: bool = True):
    conditions = (
        AsyncJob.id == job_id, AsyncJob.status == "running",
        AsyncJob.locked_by == worker_id, AsyncJob.attempts == attempt,
    )
    return conditions + ((AsyncJob.locked_at >= utcnow() - timedelta(seconds=LEASE_SECONDS),)
                         if require_live_lease else ())


def _renew(session: Session, job_id: int, worker_id: str, attempt: int) -> bool:
    return session.execute(update(AsyncJob).where(*_ownership(job_id, worker_id, attempt))
                           .values(locked_at=utcnow())).rowcount == 1


@contextmanager
def _heartbeat(job_id: int, worker_id: str, attempt: int):
    stop = threading.Event()

    def renew():
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                with SessionLocal() as session:
                    owned = _renew(session, job_id, worker_id, attempt)
                    session.commit()
                if not owned:
                    return
            except Exception:
                # A temporary DB lock must not extend ownership locally. The
                # conditional final update is authoritative if the lease expires.
                logger.exception("job lease renewal failed (job_id=%s)", job_id)

    thread = threading.Thread(target=renew, daemon=True, name=f"job-lease-{job_id}")
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def _commit_result(session: Session, result: dict) -> None:
    claim = _current_claim.get()
    if claim is None or not _complete(session, claim[0], result, worker_id=claim[1], attempt=claim[2]):
        session.rollback()
        raise LeaseLost("job ownership changed")
    # Domain writes and successful job result commit atomically. A restarted
    # worker can no longer replay a completed photo import.
    session.commit()


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
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError:
        if not idempotency_key:
            raise
        existing = session.scalar(select(AsyncJob).where(
            AsyncJob.kind == kind, AsyncJob.idempotency_key == idempotency_key))
        if existing is None:
            raise
        return existing
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
    stale_before = now - timedelta(seconds=LEASE_SECONDS)
    session.execute(update(AsyncJob).where(
        AsyncJob.status == "running", AsyncJob.locked_at < stale_before,
        AsyncJob.attempts >= AsyncJob.max_attempts,
    ).values(status="failed", error="worker lease expired after maximum attempts",
             finished_at=now, locked_at=None, locked_by=None))
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
    # CAS protects SQLite callers too; PostgreSQL additionally uses SKIP LOCKED.
    previous_status, previous_attempt = candidate.status, candidate.attempts
    changed = session.execute(update(AsyncJob).where(
        AsyncJob.id == candidate.id, AsyncJob.status == previous_status,
        AsyncJob.attempts == previous_attempt,
        or_(AsyncJob.status == "queued", AsyncJob.locked_at < stale_before),
    ).values(status="running", locked_by=worker_id, locked_at=now,
             attempts=previous_attempt + 1))
    if changed.rowcount != 1:
        return None
    session.refresh(candidate)
    return candidate


def _complete(session: Session, job_id: int, result: dict[str, Any] | None,
              *, worker_id: str, attempt: int) -> bool:
    # SQLite can hold its single writer lock throughout a domain transaction,
    # preventing the heartbeat from writing. Completion may win after expiry
    # only if no replacement attempt has claimed the row; the CAS still fences
    # every superseded worker, and commits domain changes atomically.
    return session.execute(update(AsyncJob).where(*_ownership(job_id, worker_id, attempt, require_live_lease=False))
        .values(status="succeeded", result_json=result or {}, error=None,
                finished_at=utcnow(), locked_at=None, locked_by=None)).rowcount == 1


def _fail(session: Session, job_id: int, error: str, *, worker_id: str, attempt: int) -> bool:
    job = session.get(AsyncJob, job_id)
    if job is None:
        return False
    retry = attempt < job.max_attempts
    return session.execute(update(AsyncJob).where(*_ownership(job_id, worker_id, attempt))
        .values(error=error[:2000], locked_at=None, locked_by=None,
                status="queued" if retry else "failed",
                available_at=utcnow() + timedelta(seconds=min(60, 2**min(attempt, 6))),
                finished_at=None if retry else utcnow())).rowcount == 1


def _register_builtin_handlers() -> None:
    if "exam_reports" in _HANDLERS:
        return

    def run_exam_reports(payload: dict[str, Any]) -> dict[str, Any]:
        exam_id = int(payload["exam_id"])
        from app.reports.auto_generate import generate_exam_reports
        from app.triggers import fire_post_exam_analysis

        with SessionLocal() as session:
            result = generate_exam_reports(session, exam_id)
            output = {
                "quality_report": result.quality,
                "diagnoses": result.diagnoses,
                "action_plans": result.action_plans,
                "interventions": result.interventions,
            }
            _commit_result(session, output)
            fire_post_exam_analysis(session, exam_id)
            return output

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
            if result.exam_id is None:
                raise ValueError("photo template parsing failed")
            output = {
                "exam_id": result.exam_id,
                "parse_job_id": result.parse_job_id,
                "questions": result.questions,
                "warnings": result.warnings,
            }
            _commit_result(session, output)
            return output

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
            if result.response_id is None:
                raise ValueError("photo response parsing failed")
            output = {
                "response_id": result.response_id,
                "parse_job_id": result.parse_job_id,
                "warnings": result.warnings,
            }
            _commit_result(session, output)
            return output

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
            attempt = job.attempts
            kind = job.kind
            payload = dict(job.payload_json or {})
            session.commit()
        inc("jobs_claimed_total")
        handler = _HANDLERS.get(kind)
        if handler is None:
            error = f"unknown job kind: {kind}"
            with SessionLocal() as session:
                _fail(session, job_id, error, worker_id=worker_id, attempt=attempt)
                session.commit()
            inc("jobs_failed_total")
            return True
        token = _current_claim.set((job_id, worker_id, attempt))
        try:
            with _heartbeat(job_id, worker_id, attempt):
                result = handler(payload)
        except Exception as exc:  # noqa: BLE001 - persisted for operator visibility
            logger.exception("async job %s failed (attempt=%s)", job_id, kind)
            with SessionLocal() as session:
                _fail(session, job_id, f"{type(exc).__name__}: {exc}", worker_id=worker_id, attempt=attempt)
                session.commit()
            inc("jobs_failed_total")
        else:
            with SessionLocal() as session:
                _complete(session, job_id, result, worker_id=worker_id, attempt=attempt)
                session.commit()
            inc("jobs_succeeded_total")
        finally:
            _current_claim.reset(token)
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
