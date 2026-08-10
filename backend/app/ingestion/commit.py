"""提交状态机 + 手工录入（DESIGN §5 / 架构不变量①）。

状态机：上传 → 解析中 → 待审核 → 已提交。
只有「已提交」的数据能派生证据事件、进入掌握度/归因/报告。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    BankQuestion,
    ExamResponse,
    ExamTemplate,
    KnowledgePoint,
    ResponseAnswer,
    Student,
    TemplateQuestion,
)
from app.pipeline.evidence import derive_events_for_response


@dataclass
class CommitResult:
    committed_responses: int = 0
    evidence_events: int = 0
    bank_questions: int = 0
    quality_report: bool = False
    diagnoses: int = 0
    skipped: list[str] = field(default_factory=list)


def commit_exam(session: Session, template_id: int) -> CommitResult:
    """只做不变量①：状态机迁移 + 派生证据 + 题库飞轮。不再生成报告（候选4 seam 显式化）。

    报告生成是调用方的组合步骤：
    - API commit 端点 = 产品语义「提交即自动生成」，端点显式组合 ``generate_exam_reports``；
    - 批量脚本只 commit（不生成报告）。
    """
    result = CommitResult()
    responses = list(
        session.scalars(
            select(ExamResponse).where(ExamResponse.exam_template_id == template_id)
        )
    )
    if not responses:
        result.skipped.append("该考试尚无任何作答记录")
        return result

    for response in responses:
        if response.status == "已提交":
            result.skipped.append(f"学生{response.student_id} 已提交过，跳过")
            continue
        if response.status not in ("待审核", "解析中"):
            result.skipped.append(f"学生{response.student_id} 状态异常 {response.status}")
            continue
        response.status = "已提交"
        session.flush()
        n = derive_events_for_response(session, response.id)
        result.committed_responses += 1
        result.evidence_events += n

    session.flush()
    result.bank_questions = seed_bank_from_template(session, template_id)
    return result


def seed_bank_from_template(session: Session, template_id: int) -> int:
    """题库飞轮（improvement-plan §6）：提交考试时把有标注的题目沉淀到 bank_question。

    有知识点标注的题目才入库（无标注无法被干预按 kp 检索）；kb_version 从标注 kp
    反查（ExamTemplate 未直接关联 kb_version）。幂等：同一 source_template_question_id
    不重复入库。题目随考试提交即视为已审核入库。
    """
    already = set(
        session.scalars(
            select(BankQuestion.source_template_question_id).where(
                BankQuestion.source_template_question_id.is_not(None)
            )
        )
    )
    n = 0
    for tq in session.scalars(
        select(TemplateQuestion).where(
            TemplateQuestion.exam_template_id == template_id
        )
    ):
        if tq.id in already or not tq.kps:
            continue
        kb_vid = session.scalar(
            select(KnowledgePoint.kb_version_id).where(
                KnowledgePoint.id == tq.kps[0].kp_id
            )
        )
        session.add(
            BankQuestion(
                kb_version_id=kb_vid,
                stem=tq.stem,
                q_type=tq.q_type,
                full_score=tq.full_score,
                difficulty=tq.difficulty_est,
                source_template_question_id=tq.id,
                status="已审核",
            )
        )
        n += 1
    session.flush()
    return n


def add_manual_response(
    session: Session,
    template_id: int,
    student_id: int,
    scores: dict[int, float],
) -> ExamResponse:
    """手工分数录入（P0 兜底入口）。scores: {题号: 得分}。"""
    template = session.get(ExamTemplate, template_id)
    if template is None:
        raise ValueError(f"exam_template {template_id} 不存在")
    student = session.get(Student, student_id)
    if student is None or student.class_id != template.class_id:
        raise ValueError(f"学生 {student_id} 不属于该班级")

    existing = session.scalar(
        select(ExamResponse.id).where(
            ExamResponse.exam_template_id == template_id,
            ExamResponse.student_id == student_id,
        )
    )
    if existing is not None:
        raise ValueError("该生已有本场考试的作答记录")

    # 先全量校验再落库，避免失败时残留半成品记录
    questions = list(
        session.scalars(
            select(TemplateQuestion).where(
                TemplateQuestion.exam_template_id == template_id
            )
        )
    )
    for tq in questions:
        score = scores.get(tq.idx, 0.0)
        if score < 0 or score > tq.full_score:
            raise ValueError(f"第{tq.idx}题分数越界：{score}（满分 {tq.full_score}）")

    response = ExamResponse(
        exam_template_id=template_id,
        student_id=student_id,
        source="manual",
        status="待审核",
    )
    session.add(response)
    session.flush()

    total = 0.0
    for tq in questions:
        score = scores.get(tq.idx, 0.0)
        total += score
        session.add(
            ResponseAnswer(
                exam_response_id=response.id,
                template_question_id=tq.id,
                score=score,
            )
        )
    response.total_score = round(total, 2)
    session.flush()
    return response
