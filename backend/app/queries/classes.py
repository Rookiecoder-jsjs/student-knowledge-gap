"""班级/进度浏览聚合（架构修复 候选2：列表端点的 N+1 → 批量取）。

``classes_list``：每班学生数/考试数各一次 group_by 聚合；
``progress_list``：一次 in_ 取全部 kp 元数据。纯查询，不感知 HTTP。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Class,
    ExamTemplate,
    KnowledgePoint,
    Student,
    TeachingProgress,
)


def classes_list(session: Session) -> list[dict]:
    out = []
    classes = list(session.scalars(select(Class).order_by(Class.id)))
    student_counts = dict(
        session.execute(
            select(Student.class_id, func.count(Student.id)).group_by(Student.class_id)
        ).all()
    )
    exam_counts = dict(
        session.execute(
            select(ExamTemplate.class_id, func.count(ExamTemplate.id)).group_by(
                ExamTemplate.class_id
            )
        ).all()
    )
    for clazz in classes:
        out.append(
            {
                "class_id": clazz.id,
                "name": clazz.name,
                "grade": clazz.grade,
                "subject": clazz.subject,
                "school_id": clazz.school_id,
                "student_count": student_counts.get(clazz.id, 0),
                "exam_count": exam_counts.get(clazz.id, 0),
            }
        )
    return out


def progress_list(session: Session, class_id: int) -> list[dict]:
    rows = list(
        session.scalars(
            select(TeachingProgress)
            .where(TeachingProgress.class_id == class_id)
            .order_by(TeachingProgress.taught_at, TeachingProgress.kp_id)
        )
    )
    kp_ids = {p.kp_id for p in rows}
    kps: dict[int, KnowledgePoint] = {}
    if kp_ids:
        kps = {
            kp.id: kp
            for kp in session.scalars(
                select(KnowledgePoint).where(KnowledgePoint.id.in_(kp_ids))
            )
        }
    out = []
    for p in rows:
        kp = kps.get(p.kp_id)
        out.append(
            {
                "kp_id": p.kp_id,
                "code": kp.code if kp else f"kp#{p.kp_id}",
                "name": kp.name if kp else "",
                "taught_at": str(p.taught_at),
                "archived": bool(kp.archived) if kp else False,
            }
        )
    return out