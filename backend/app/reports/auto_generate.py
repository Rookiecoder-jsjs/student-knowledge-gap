"""考试提交后自动生成并落库班级质量报告 + 已参加学生诊断（plan §1.2）。

触发：commit_exam 提交后同步调用。基础报告不含 LLM（秒级~十几秒）；
AI 解读在首次查看时生成并缓存到 narrative_markdown，同 prompt 版本直接复用。

幂等：同一场考试重复生成按 (exam_id, type[, student_id]) 替换，不产生重复行。
失败降级：整体包在 savepoint 里，报告生成失败仅回滚 savepoint，
不影响考试提交本身（提交是主流程，报告是附带产物）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.kb.graph import KpGraph
from app.kb.resolver import KbNotActiveError, active_kb
from app.models import (
    ExamResponse,
    ExamTemplate,
    KbVersion,
    Report,
    Student,
)
from app.pipeline.attribution import materialize_attribution_verdicts
from app.pipeline.mastery import get_events_batch
from app.pipeline.weakness import assess_student_kps
from app.reports.quality_analysis import generate_quality_analysis
from app.reports.student_diagnosis import generate_student_diagnosis

logger = logging.getLogger(__name__)

_REPORT_TYPES = ("quality_analysis", "student_diagnosis")


@dataclass
class ExamReportResult:
    """一场考试自动生成的结果摘要（提交响应带回）。"""

    quality: bool = False
    diagnoses: int = 0


def generate_exam_reports(session: Session, exam_id: int) -> ExamReportResult:
    """为一场考试自动生成班级质量报告 + 已参加学生诊断并落库。

    best-effort：内部失败回滚 savepoint 并返回全 False 结果，绝不向提交抛异常。
    """
    template = session.get(ExamTemplate, exam_id)
    if template is None:
        return ExamReportResult()
    kb = _active_kb(session)
    if kb is None:
        logger.warning("考试 %s 报告生成跳过：无 active 知识库版本", exam_id)
        return ExamReportResult()
    graph = KpGraph(session, kb.id)
    try:
        with session.begin_nested():
            result = _generate_exam_reports(session, graph, exam_id)
        return result
    except Exception:
        logger.exception("考试 %s 报告生成失败（不影响提交）", exam_id)
        return ExamReportResult()


def _generate_exam_reports(session: Session, graph: KpGraph, exam_id: int) -> ExamReportResult:
    template = session.get(ExamTemplate, exam_id)
    as_of = datetime.combine(template.exam_date, time(23, 59))
    class_id = template.class_id

    # 只为已提交本场考试的学生生成诊断（未参加者不与本场报告关联）
    student_ids = list(
        session.scalars(
            select(ExamResponse.student_id).where(
                ExamResponse.exam_template_id == exam_id,
                ExamResponse.status == "已提交",
            )
        )
    )
    if not student_ids:
        return ExamReportResult()
    students = list(session.scalars(select(Student).where(Student.id.in_(student_ids))))

    # 先清掉本场旧报告再重建（幂等替换）；失败时 savepoint 整体回滚、旧报告保留
    session.execute(
        delete(Report).where(
            Report.exam_id == exam_id,
            Report.type.in_(_REPORT_TYPES),
        )
    )

    # 一次批量预取全班×全 kp 证据，班级报告与各生诊断共享（避免 N 次全表扫描）
    events_by_sk = get_events_batch(
        session,
        [s.id for s in students],
        list(graph.grade7_kp_ids()),
        as_of,
    )

    generate_quality_analysis(
        session, graph, class_id, exam_id, narrative=False, events_by_sk=events_by_sk
    )
    for s in students:
        # 候选1：评估一次，诊断（derive-on-read）与物化（尾步）共享，省掉重复计算。
        # 诊断不再依赖「打底」；物化在生成成功后同步执行，供 override-by-id/闭合率统计。
        assessments = assess_student_kps(
            session, graph, s.id, class_id, as_of, events_by_sk=events_by_sk
        )
        generate_student_diagnosis(
            session,
            graph,
            s.id,
            as_of=as_of,
            assessments=assessments,
            narrative=False,
            events_by_sk=events_by_sk,
            exam_id=exam_id,
        )
        materialize_attribution_verdicts(
            session, graph, s.id, class_id, as_of, assessments=assessments
        )
    session.flush()
    return ExamReportResult(quality=True, diagnoses=len(students))


def _active_kb(session: Session) -> KbVersion | None:
    """active 知识库（strict 策略统一在 kb.resolver，候选5a）。

    报告生成是 best-effort：strict 无 active / 无任何版本均返回 None，报告跳过、不影响提交。
    """
    try:
        return active_kb(session)
    except KbNotActiveError:
        logger.warning("考试报告生成跳过：SC_KB_STRICT_ACTIVE 下无 active 知识库版本")
        return None
