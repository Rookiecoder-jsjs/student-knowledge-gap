"""班级概览聚合（架构修复 候选2：从 routes.py 抽出的查询层深模块）。

纯查询，不做 HTTP 语义：
- ``grade7_kp_ids`` 由调用方解析（无 active kb 时传空集 → progress 为 {0, 0}），
  本模块不抛 HTTPException、不感知依赖注入；
- 返回结构与原端点一致，供一级「班级概览」页一次拉取，避免前端 N+1。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Class,
    ExamResponse,
    ExamTemplate,
    QuestionKp,
    Student,
    TeachingProgress,
    TemplateQuestion,
)


def classes_overview(session: Session, grade7_kp_ids: set[int]) -> dict:
    """所有班级的轻量概览（待办考试数 / 最近一场考试状态 / 教学进度覆盖）。

    ``grade7_kp_ids``：分析层同分母（grade7 全部 kp）。active kb 缺失时调用方传空集，
    进度展示 {taught: 0, total: 0}，一级页面不因未导入知识库整页 500。
    """
    out = []
    for clazz in session.scalars(select(Class).order_by(Class.id)):
        student_n = session.scalar(
            select(func.count(Student.id)).where(Student.class_id == clazz.id)
        ) or 0
        exams = session.scalars(
            select(ExamTemplate)
            .where(ExamTemplate.class_id == clazz.id)
            .order_by(ExamTemplate.exam_date.desc(), ExamTemplate.id.desc())
        ).all()

        todo_count = 0
        latest_exam = None
        for tpl in exams:
            status_counts = dict(
                session.execute(
                    select(ExamResponse.status, func.count(ExamResponse.id))
                    .where(ExamResponse.exam_template_id == tpl.id)
                    .group_by(ExamResponse.status)
                ).all()
            )
            unreviewed = session.scalar(
                select(func.count(QuestionKp.id))
                .join(TemplateQuestion, QuestionKp.template_question_id == TemplateQuestion.id)
                .where(TemplateQuestion.exam_template_id == tpl.id)
                .where(QuestionKp.reviewed_at.is_(None))
            ) or 0
            pending = status_counts.get("待审核", 0)
            if latest_exam is None:
                latest_exam = {
                    "exam_id": tpl.id,
                    "name": tpl.name,
                    "exam_date": str(tpl.exam_date),
                    "type": tpl.type,
                    "submitted": status_counts.get("已提交", 0),
                    "pending": pending,
                }
            if unreviewed > 0 or pending > 0:
                todo_count += 1

        taught = 0
        if grade7_kp_ids:
            taught = session.scalar(
                select(func.count(TeachingProgress.id))
                .where(TeachingProgress.class_id == clazz.id)
                .where(TeachingProgress.kp_id.in_(grade7_kp_ids))
            ) or 0

        out.append(
            {
                "class_id": clazz.id,
                "name": clazz.name,
                "grade": clazz.grade,
                "subject": clazz.subject,
                "school_id": clazz.school_id,
                "student_count": student_n,
                "exam_count": len(exams),
                "todo_count": todo_count,
                "latest_exam": latest_exam,
                "progress": {"taught": taught, "total": len(grade7_kp_ids)},
            }
        )
    return {"classes": out}
