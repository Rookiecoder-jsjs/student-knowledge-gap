"""班级质量报告：纯计算模型（架构修复 候选3 分层第一层）。

compute / render / persist 三层，接口即测试面：
- ``compute_quality_model``：证据/作答 → 统计模型（无 markdown、无落库、无 LLM）；
- ``quality_render.render_quality_markdown``：模型 → markdown（纯字符串）；
- ``quality_analysis.generate_quality_analysis``：组合三层并落 Report。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import CLASS_COMMON_WEAK_RATIO
from app.kb.graph import KpGraph
from app.models import (
    Class,
    EvidenceEvent,
    ExamResponse,
    ExamTemplate,
    ResponseAnswer,
    Student,
    TemplateQuestion,
)
from app.pipeline.mastery import get_events_batch
from app.pipeline.weakness import KpAssessment, assess_student_kps


@dataclass
class QualityReportModel:
    """班级质量报告的全部数字与结构（纯数据，可快照、可渲染）。"""

    class_name: str
    exam_name: str
    exam_type: str
    exam_date: str
    committed: int
    pending: int
    totals: list[float]
    full_total: float
    question_rates: list[dict]
    kp_stats: dict[int, dict]
    common_weak: list[dict]
    # 教学行动方向摘要（intervention-loop-design §5）：auto_generate 在干预建议
    # 生成后回填（quality 报告先建、行动行后建，经 snapshot 注入而非模型计算）。
    actions: list[dict] | None = None


def compute_quality_model(
    session: Session,
    graph: KpGraph,
    class_id: int,
    exam_id: int,
    *,
    events_by_sk: dict[tuple[int, int], list[EvidenceEvent]] | None = None,
    actions: list[dict] | None = None,
) -> QualityReportModel:
    """纯计算：从证据/作答算统计。无 markdown、无落库、无 LLM。

    bonus（N+1 修复）：逐题×逐作答的 ``ResponseAnswer`` 由原 Q×R 次 scalar 查询
    改为一次 IN 批量取回，按 ``(exam_response_id, template_question_id)`` 分组。
    """
    clazz = session.get(Class, class_id)
    template = session.get(ExamTemplate, exam_id)
    as_of = datetime.combine(template.exam_date, time(23, 59))

    students = list(
        session.scalars(select(Student).where(Student.class_id == class_id))
    )
    committed = {
        r.student_id: r
        for r in session.scalars(
            select(ExamResponse).where(
                ExamResponse.exam_template_id == exam_id,
                ExamResponse.status == "已提交",
            )
        )
    }
    pending = len(students) - len(committed)

    totals = [r.total_score for r in committed.values()]
    questions = list(
        session.scalars(
            select(TemplateQuestion)
            .where(TemplateQuestion.exam_template_id == exam_id)
            .order_by(TemplateQuestion.idx)
        )
    )
    full_total = sum(q.full_score for q in questions)

    # ---- 逐题得分率（N+1 修复：一次批量取全部作答） ----
    ans_by_key: dict[tuple[int, int], ResponseAnswer] = {}
    if committed and questions:
        rows = session.scalars(
            select(ResponseAnswer).where(
                ResponseAnswer.exam_response_id.in_(committed.keys()),
                ResponseAnswer.template_question_id.in_([q.id for q in questions]),
            )
        ).all()
        ans_by_key = {(a.exam_response_id, a.template_question_id): a for a in rows}

    q_rates: list[dict] = []
    for q in questions:
        rates = []
        for r in committed.values():
            ans = ans_by_key.get((r.id, q.id))
            if ans is not None and q.full_score > 0:
                rates.append(ans.score / q.full_score)
        rate = sum(rates) / len(rates) if rates else None
        kp_names = ", ".join(graph.kp(qk.kp_id).name for qk in q.kps)
        q_rates.append(
            {
                "idx": q.idx,
                "q_type": q.q_type,
                "full_score": q.full_score,
                "rate": rate,
                "kps": kp_names,
                "low": rate is not None and rate < 0.6,
            }
        )

    # ---- 班级知识点掌握度（derive-on-read） ----
    # events_by_sk 由调用方预取传入（auto_generate 提交时批量生成共享）；缺省则内部预取。
    if events_by_sk is None:
        events_by_sk = get_events_batch(
            session, [s.id for s in students], list(graph.grade7_kp_ids()), as_of
        )
    per_student: dict[int, list[KpAssessment]] = {
        sid: assess_student_kps(
            session, graph, sid, class_id, as_of, events_by_sk=events_by_sk
        )
        for sid in committed
    }
    kp_stats: dict[int, dict] = {}
    for assessments in per_student.values():
        for a in assessments:
            if a.gate is not None or a.mastery is None:
                continue
            st = kp_stats.setdefault(
                a.kp_id,
                {"code": a.kp_code, "name": a.kp_name, "values": [], "weak": 0, "n": 0},
            )
            st["values"].append(a.mastery)
            st["n"] += 1
            if a.is_weak:
                st["weak"] += 1

    common_weak: list[dict] = []
    for kp_id, st in kp_stats.items():
        if st["n"] >= 4:
            share = st["weak"] / st["n"]
            avg = sum(st["values"]) / len(st["values"])
            if share >= CLASS_COMMON_WEAK_RATIO:
                common_weak.append(
                    {
                        "code": st["code"],
                        "name": st["name"],
                        "class_avg": avg,
                        "weak_share": share,
                        "n": st["n"],
                    }
                )
    common_weak.sort(key=lambda d: d["class_avg"])

    return QualityReportModel(
        class_name=clazz.name,
        exam_name=template.name,
        exam_type=template.type,
        exam_date=str(template.exam_date),
        committed=len(committed),
        pending=pending,
        totals=totals,
        full_total=full_total,
        question_rates=q_rates,
        kp_stats=kp_stats,
        common_weak=common_weak,
        actions=actions,
    )
