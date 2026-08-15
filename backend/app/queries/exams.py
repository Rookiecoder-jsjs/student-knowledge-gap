"""考试浏览聚合（架构修复 候选2：列表端点的 N+1 → 批量取）。

- ``exams_list``：状态计数/未审标注/题数各一次 group_by 聚合；
- ``exam_detail``：逐题逐标签的 kp 回查 → 一次 in_ 取；
- ``exam_responses``：低置信题计数 → 一次 group_by 聚合。

纯查询，不感知 HTTP；AUTO_PASS 语义（<0.6 强制人工）来自拍照解析阈值。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion.photo import AUTO_PASS
from app.models import (
    ExamResponse,
    ExamTemplate,
    KnowledgePoint,
    QuestionKp,
    ResponseAnswer,
    Student,
    TemplateQuestion,
)


def exams_list(session: Session, class_id: int | None = None) -> list[dict]:
    stmt = select(ExamTemplate).order_by(ExamTemplate.exam_date.desc(), ExamTemplate.id)
    if class_id is not None:
        stmt = stmt.where(ExamTemplate.class_id == class_id)
    exams = list(session.scalars(stmt))
    exam_ids = [t.id for t in exams]
    if not exam_ids:
        return []

    # 每场×每个作答状态的计数
    status_counts: dict[int, dict[str, int]] = {eid: {} for eid in exam_ids}
    for eid, status, n in session.execute(
        select(
            ExamResponse.exam_template_id,
            ExamResponse.status,
            func.count(ExamResponse.id),
        )
        .where(ExamResponse.exam_template_id.in_(exam_ids))
        .group_by(ExamResponse.exam_template_id, ExamResponse.status)
    ):
        status_counts[eid][status] = n

    # 未审标注数
    unreviewed: dict[int, int] = dict(
        session.execute(
            select(
                TemplateQuestion.exam_template_id, func.count(QuestionKp.id)
            )
            .join(QuestionKp, QuestionKp.template_question_id == TemplateQuestion.id)
            .where(
                TemplateQuestion.exam_template_id.in_(exam_ids),
                QuestionKp.reviewed_at.is_(None),
            )
            .group_by(TemplateQuestion.exam_template_id)
        ).all()
    )

    # 每场题数（避免 len(tpl.questions) 触发逐场懒加载）
    question_counts: dict[int, int] = dict(
        session.execute(
            select(
                TemplateQuestion.exam_template_id, func.count(TemplateQuestion.id)
            )
            .where(TemplateQuestion.exam_template_id.in_(exam_ids))
            .group_by(TemplateQuestion.exam_template_id)
        ).all()
    )

    out = []
    for tpl in exams:
        out.append(
            {
                "exam_id": tpl.id,
                "class_id": tpl.class_id,
                "name": tpl.name,
                "exam_date": str(tpl.exam_date),
                "type": tpl.type,
                "source": tpl.source,
                "question_count": question_counts.get(tpl.id, 0),
                "response_counts": status_counts[tpl.id],
                "unreviewed_tags": unreviewed.get(tpl.id, 0),
            }
        )
    return out


def exam_detail(session: Session, exam_id: int) -> dict | None:
    """单场考试详情（含每题标注）。考试不存在返回 None（调用方决定 404）。"""
    tpl = session.get(ExamTemplate, exam_id)
    if tpl is None:
        return None
    questions = sorted(tpl.questions, key=lambda x: x.idx)
    tag_kp_ids = {tag.kp_id for q in questions for tag in q.kps}
    kps: dict[int, KnowledgePoint] = {}
    if tag_kp_ids:
        kps = {
            kp.id: kp
            for kp in session.scalars(
                select(KnowledgePoint).where(KnowledgePoint.id.in_(tag_kp_ids))
            )
        }
    out_questions = []
    for q in questions:
        kp_list = []
        for tag in q.kps:
            kp = kps.get(tag.kp_id)
            kp_list.append(
                {
                    "tag_id": tag.id,
                    "code": kp.code if kp else "",
                    "name": kp.name if kp else "",
                    "weight": tag.weight,
                    "source": tag.source,
                    "confidence": tag.confidence,
                    "reviewed": tag.reviewed_at is not None,
                    "reviewed_by": tag.reviewed_by,
                }
            )
        out_questions.append(
            {
                "question_id": q.id,
                "idx": q.idx,
                "stem": q.stem,
                "q_type": q.q_type,
                "full_score": q.full_score,
                "cog_level": q.cog_level,
                "n_options": q.n_options,
                "kps": kp_list,
            }
        )
    return {
        "exam_id": tpl.id,
        "class_id": tpl.class_id,
        "name": tpl.name,
        "exam_date": str(tpl.exam_date),
        "type": tpl.type,
        "source": tpl.source,
        "questions": out_questions,
    }


def exam_responses(session: Session, exam_id: int) -> dict | None:
    """学生×作答状态矩阵（名单原序；低置信题计数一次聚合）。考试不存在返回 None。"""
    tpl = session.get(ExamTemplate, exam_id)
    if tpl is None:
        return None
    students = list(
        session.scalars(
            select(Student).where(Student.class_id == tpl.class_id).order_by(Student.id)
        )
    )
    responses = {
        r.student_id: r
        for r in session.scalars(
            select(ExamResponse).where(ExamResponse.exam_template_id == exam_id)
        )
    }
    resp_ids = [r.id for r in responses.values()]
    low_counts: dict[int, int] = {}
    if resp_ids:
        low_counts = dict(
            session.execute(
                select(
                    ResponseAnswer.exam_response_id, func.count(ResponseAnswer.id)
                )
                .where(
                    ResponseAnswer.exam_response_id.in_(resp_ids),
                    ResponseAnswer.parse_confidence < AUTO_PASS,
                )
                .group_by(ResponseAnswer.exam_response_id)
            ).all()
        )

    rows = []
    counts = {"未采集": 0, "待审核": 0, "已提交": 0}
    for stu in students:
        resp = responses.get(stu.id)
        if resp is None:
            row_status = "未采集"
            row = {
                "student_id": stu.id,
                "name_or_alias": stu.name_or_alias,
                "status": row_status,
                "response_id": None,
                "total_score": None,
                "low_confidence_count": 0,
            }
        else:
            row_status = resp.status if resp.status in counts else "待审核"
            row = {
                "student_id": stu.id,
                "name_or_alias": stu.name_or_alias,
                "status": row_status,
                "response_id": resp.id,
                "total_score": resp.total_score,
                "low_confidence_count": low_counts.get(resp.id, 0),
            }
        counts[row_status] += 1
        rows.append(row)
    return {"exam_id": exam_id, "summary": counts, "responses": rows}