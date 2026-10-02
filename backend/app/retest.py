"""复测日期是计划事实；是否获得后续证据按读取时点推导，不另存效果。"""

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import EvidenceEvent, ExamResponse, Intervention, ResponseAnswer, Student


def default_retest_date(done_at: datetime) -> date:
    return done_at.date() + timedelta(days=7)


def validate_retest_date(due: date, today: date) -> None:
    if not today <= due <= today + timedelta(days=90):
        raise ValueError("复测日期须在今天至未来 90 天内")


def retest_progress(session: Session, iv: Intervention, now: datetime | None = None) -> dict:
    when = now or datetime.now()
    base = {"retest_due_date": iv.retest_due_date.isoformat() if iv.retest_due_date else None,
            "retest_status": None, "retested_students": 0, "target_students": 0}
    if iv.status != "done" or iv.done_at is None or iv.retest_due_date is None:
        return base
    targets = select(Student.id).where(Student.class_id == iv.class_id)
    if iv.student_id is not None:
        targets = targets.where(Student.id == iv.student_id)
    count = session.scalar(select(func.count()).select_from(targets.subquery())) or 0
    covered = session.scalar(
        select(func.count(func.distinct(EvidenceEvent.student_id)))
        .join(ResponseAnswer, EvidenceEvent.response_answer_id == ResponseAnswer.id)
        .join(ExamResponse, ResponseAnswer.exam_response_id == ExamResponse.id)
        .where(EvidenceEvent.student_id.in_(targets), EvidenceEvent.kp_id == iv.kp_id,
               EvidenceEvent.occurred_at > iv.done_at, EvidenceEvent.occurred_at <= when,
               EvidenceEvent.weight > 0, ExamResponse.status == "已提交",
               ExamResponse.student_id == EvidenceEvent.student_id,
               ExamResponse.exam_template_id != iv.exam_id)
    ) or 0
    state = "verified" if count > 0 and covered >= count else (
        "overdue" if iv.retest_due_date < when.date() else "scheduled"
    )
    return {**base, "retest_status": state, "retested_students": covered, "target_students": count}
