"""证据事件派生（DESIGN §6）——commit 时调用，只追加不修改（不变量②）。

规则：
- value = 得分率，选择题经猜测校正 e = max(0, (p−g)/(1−g)), g = 1/选项数；
- weight = 来源权重 × 题内知识点分摊权重 × 级联降权(×0.5) × 异常考试降权(×0.5)；
- class_avg_rate = 该题班级平均得分率（免费客观难度参照）；
- 幂等：同一 response_answer 不重复派生。
"""

from __future__ import annotations

import math
from datetime import datetime, time
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import (
    ALGO_VERSION,
    ANOMALY_SCORE_DROP,
    ANOMALY_WEIGHT_FACTOR,
    CASCADE_WEIGHT_FACTOR,
    DEFAULT_CHOICE_OPTIONS,
    EVIDENCE_MIX_PENALTY,
    SOURCE_TYPE_WEIGHT,
)
from app.models import (
    EvidenceEvent,
    ExamResponse,
    ExamTemplate,
    ResponseAnswer,
    TemplateQuestion,
)


def derive_events_for_response(session: Session, response_id: int) -> int:
    """为一条「已提交」作答派生证据事件，返回新增事件数。"""
    response = session.get(ExamResponse, response_id)
    if response is None:
        raise ValueError(f"exam_response {response_id} 不存在")
    if response.status != "已提交":
        raise ValueError(
            f"架构不变量①：分析层只读已提交数据（当前状态 {response.status}）"
        )

    template = session.get(ExamTemplate, response.exam_template_id)
    source_weight = SOURCE_TYPE_WEIGHT.get(template.type, 0.8)
    occurred = datetime.combine(template.exam_date, time(12, 0))

    # 幂等：已有该作答的证据事件则跳过
    existing = session.scalar(
        select(EvidenceEvent.id)
        .join(ResponseAnswer, ResponseAnswer.id == EvidenceEvent.response_answer_id)
        .where(ResponseAnswer.exam_response_id == response.id)
        .limit(1)
    )
    if existing is not None:
        return 0

    anomaly_factor = _anomaly_factor(session, response, template)
    class_rates = _class_question_rates(session, template)

    n_new = 0
    for answer in session.scalars(
        select(ResponseAnswer).where(ResponseAnswer.exam_response_id == response.id)
    ):
        question = session.get(TemplateQuestion, answer.template_question_id)
        if question is None or question.full_score <= 0 or not question.kps:
            continue  # 无标注的题目不进分析（标注闸门前置）

        rate = max(0.0, min(1.0, answer.score / question.full_score))
        if question.q_type == "选择":
            n_opt = question.n_options or DEFAULT_CHOICE_OPTIONS
            g = 1.0 / max(2, n_opt)
            value = max(0.0, (rate - g) / (1.0 - g))
        else:
            value = rate

        cascade = CASCADE_WEIGHT_FACTOR if answer.cascade_flag else 1.0
        class_rate = class_rates.get(question.id)

        n_kps = len(question.kps)
        # 混合题（多 kp）失分归属不确定，按 (1/√N)^penalty 降权减少等量污染（§1.4-C）。
        # penalty=0（默认）-> mix=1 行为不变；单 kp 题精确归属，不打折。
        mix = (
            (1.0 / math.sqrt(n_kps)) ** EVIDENCE_MIX_PENALTY
            if EVIDENCE_MIX_PENALTY > 0 and n_kps > 1
            else 1.0
        )
        for qkp in question.kps:
            weight = source_weight * qkp.weight * cascade * anomaly_factor * mix
            session.add(
                EvidenceEvent(
                    student_id=response.student_id,
                    kp_id=qkp.kp_id,
                    response_answer_id=answer.id,
                    source_type=template.type,
                    value=round(value, 6),
                    weight=round(weight, 6),
                    cog_level=question.cog_level,
                    class_avg_rate=class_rate,
                    occurred_at=occurred,
                    algo_version=ALGO_VERSION,
                )
            )
            n_new += 1

    session.flush()
    return n_new


def _class_question_rates(session: Session, template: ExamTemplate) -> dict[int, float]:
    """班级该题平均得分率（基于同一模板全部已提交作答）。

    G9：批量取题 + 批量取答案，查询数不随学生/题数膨胀（原逐 response 懒加载 answers
    + 逐 answer session.get(TemplateQuestion) 为 N+1）。
    """
    # 1) 模板全部题目一次取出（id -> full_score）
    qmap = {
        q.id: q
        for q in session.scalars(
            select(TemplateQuestion).where(
                TemplateQuestion.exam_template_id == template.id
            )
        )
    }
    if not qmap:
        return {}
    # 2) 全部已提交作答的答案一次取出（join 避免逐 response 懒加载）
    rows = session.execute(
        select(ResponseAnswer.template_question_id, ResponseAnswer.score)
        .join(ExamResponse, ExamResponse.id == ResponseAnswer.exam_response_id)
        .where(
            ExamResponse.exam_template_id == template.id,
            ExamResponse.status == "已提交",
        )
    ).all()
    rates: dict[int, list[float]] = {}
    for qid, score in rows:
        q = qmap.get(qid)
        if q is not None and q.full_score > 0:
            rates.setdefault(qid, []).append(score / q.full_score)
    return {qid: round(mean(v), 4) for qid, v in rates.items() if v}


def _anomaly_factor(
    session: Session, response: ExamResponse, template: ExamTemplate
) -> float:
    """异常考试：总分相对该生历史均值骤降 >30% → 证据降权（DESIGN §6）。"""
    history = session.execute(
        select(ExamResponse.total_score)
        .join(ExamTemplate, ExamTemplate.id == ExamResponse.exam_template_id)
        .where(
            ExamResponse.student_id == response.student_id,
            ExamResponse.status == "已提交",
            ExamTemplate.exam_date < template.exam_date,
        )
    ).scalars().all()
    if not history:
        return 1.0
    avg_prev = mean(history)
    if avg_prev <= 0:
        return 1.0
    drop = (avg_prev - response.total_score) / avg_prev
    return ANOMALY_WEIGHT_FACTOR if drop > ANOMALY_SCORE_DROP else 1.0
