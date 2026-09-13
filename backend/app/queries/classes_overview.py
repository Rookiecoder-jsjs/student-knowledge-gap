"""班级概览聚合（架构修复 候选2：从 routes.py 抽出的查询层深模块）。

纯查询，不做 HTTP 语义：
- 知识点分母由调用方解析（无 active kb 时传空集 → progress 为 {0, 0}），
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


def classes_overview(
    session: Session,
    grade7_kp_ids: set[int],
    kp_ids_by_class: dict[int, set[int]] | None = None,
) -> dict:
    """所有班级的轻量概览（待办考试数 / 最近一场考试状态 / 教学进度覆盖）。

    ``grade7_kp_ids``：兼容旧调用的默认同分母；``kp_ids_by_class`` 提供后按
    班级学科/年级分别计算，避免多学科或不同年级共用一个知识库分母。
    """
    classes = list(session.scalars(select(Class).order_by(Class.id)))
    if not classes:
        return {"classes": []}

    class_ids = [clazz.id for clazz in classes]
    student_counts = dict(
        session.execute(
            select(Student.class_id, func.count(Student.id))
            .where(Student.class_id.in_(class_ids))
            .group_by(Student.class_id)
        ).all()
    )

    # Load all templates once; the global ordering keeps the first item in each
    # class bucket as the latest exam, matching the previous per-class query.
    exams = list(
        session.scalars(
            select(ExamTemplate)
            .where(ExamTemplate.class_id.in_(class_ids))
            .order_by(ExamTemplate.exam_date.desc(), ExamTemplate.id.desc())
        )
    )
    exams_by_class: dict[int, list[ExamTemplate]] = {}
    for exam in exams:
        exams_by_class.setdefault(exam.class_id, []).append(exam)

    exam_ids = [exam.id for exam in exams]
    status_counts: dict[int, dict[str, int]] = {exam_id: {} for exam_id in exam_ids}
    unreviewed_counts: dict[int, int] = {}
    if exam_ids:
        for exam_id, status, count in session.execute(
            select(
                ExamResponse.exam_template_id,
                ExamResponse.status,
                func.count(ExamResponse.id),
            )
            .where(ExamResponse.exam_template_id.in_(exam_ids))
            .group_by(ExamResponse.exam_template_id, ExamResponse.status)
        ):
            status_counts[exam_id][status] = count

        unreviewed_counts = dict(
            session.execute(
                select(TemplateQuestion.exam_template_id, func.count(QuestionKp.id))
                .join(QuestionKp, QuestionKp.template_question_id == TemplateQuestion.id)
                .where(
                    TemplateQuestion.exam_template_id.in_(exam_ids),
                    QuestionKp.reviewed_at.is_(None),
                )
                .group_by(TemplateQuestion.exam_template_id)
            ).all()
        )

    class_kp_ids = kp_ids_by_class or {
        clazz.id: set(grade7_kp_ids) for clazz in classes
    }
    all_kp_ids = set().union(*class_kp_ids.values()) if class_kp_ids else set()
    taught_by_class: dict[int, set[int]] = {}
    if all_kp_ids:
        for class_id, kp_id in session.execute(
            select(TeachingProgress.class_id, TeachingProgress.kp_id).where(
                TeachingProgress.class_id.in_(class_ids),
                TeachingProgress.kp_id.in_(all_kp_ids),
            )
        ):
            taught_by_class.setdefault(class_id, set()).add(kp_id)

    out = []
    for clazz in classes:
        class_exams = exams_by_class.get(clazz.id, [])
        todo_count = sum(
            1
            for exam in class_exams
            if unreviewed_counts.get(exam.id, 0) > 0
            or status_counts[exam.id].get("待审核", 0) > 0
        )
        latest_exam = None
        if class_exams:
            tpl = class_exams[0]
            latest_status = status_counts[tpl.id]
            latest_exam = {
                "exam_id": tpl.id,
                "name": tpl.name,
                "exam_date": str(tpl.exam_date),
                "type": tpl.type,
                "submitted": latest_status.get("已提交", 0),
                "pending": latest_status.get("待审核", 0),
            }

        class_scope = class_kp_ids.get(clazz.id, set())
        out.append(
            {
                "class_id": clazz.id,
                "name": clazz.name,
                "grade": clazz.grade,
                "subject": clazz.subject,
                "school_id": clazz.school_id,
                "student_count": student_counts.get(clazz.id, 0),
                "exam_count": len(class_exams),
                "todo_count": todo_count,
                "latest_exam": latest_exam,
                "progress": {
                    "taught": len(taught_by_class.get(clazz.id, set()) & class_scope),
                    "total": len(class_scope),
                },
            }
        )
    return {"classes": out}
