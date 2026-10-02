"""诊断证据分页回溯；只展示当前学生的已提交作答，不暴露同学成绩。"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import EvidenceEvent, ExamResponse, ExamTemplate, ResponseAnswer, TemplateQuestion
from app.pipeline.mastery import effective_weight


def diagnosis_evidence(session: Session, student_id: int, kp_id: int,
                       as_of: datetime, offset: int, limit: int) -> dict:
    query = (
        select(EvidenceEvent, ResponseAnswer, TemplateQuestion, ExamTemplate)
        .join(ResponseAnswer, EvidenceEvent.response_answer_id == ResponseAnswer.id)
        .join(ExamResponse, ResponseAnswer.exam_response_id == ExamResponse.id)
        .join(TemplateQuestion, ResponseAnswer.template_question_id == TemplateQuestion.id)
        .join(ExamTemplate, ExamResponse.exam_template_id == ExamTemplate.id)
        .where(EvidenceEvent.student_id == student_id, EvidenceEvent.kp_id == kp_id,
               ExamResponse.student_id == student_id, ExamResponse.status == "已提交",
               EvidenceEvent.occurred_at <= as_of)
    )
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.execute(query.order_by(EvidenceEvent.occurred_at.desc(), EvidenceEvent.id.desc())
                           .offset(offset).limit(limit))
    return {
        "student_id": student_id, "kp_id": kp_id, "as_of": as_of.date().isoformat(),
        "total": total, "offset": offset, "limit": limit, "has_more": offset + limit < total,
        "items": [{
            "id": ev.id, "exam_id": exam.id, "exam_name": exam.name,
            "exam_date": exam.exam_date.isoformat(), "source_type": ev.source_type,
            "question_idx": q.idx, "stem": q.stem, "score": answer.score,
            "full_score": q.full_score, "value": ev.value, "weight": ev.weight,
            "effective_weight": round(effective_weight(ev, as_of), 4),
            "cog_level": ev.cog_level, "cascade_flag": answer.cascade_flag,
            "occurred_at": ev.occurred_at.isoformat(),
        } for ev, answer, q, exam in rows],
    }
