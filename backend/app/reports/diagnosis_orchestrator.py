"""报告 get-or-generate 编排（架构修复 候选2：diagnosis 三分支收进领域层）。

职责边界（seam）：
- ``get_or_generate_diagnosis``：三分支决策（指定考试 / 最近一场 / 自定义 as_of）
  + 生成后物化尾步（``materialize_attribution_verdicts``）；
- ``get_or_generate_quality_report``：质量报告 get-or-generate；
- ``get_or_create_narrative``：AI 解读段缓存（LLM 可用时生成写入，一次永久）；
- ``_latest_stored_diagnosis`` / ``_day_end``：内部实现。

本模块不依赖 api/（HTTP 层只做参数解析与异常翻译，领域层抛 ValueError）。
诊断渲染本身 derive-on-read（候选1），物化仅为 override-by-id / 闭合率统计落行，
物化失败不影响渲染。

时间约定与 api.deps._as_dt 一致：naive 本地时刻作证据截止——utcnow 在东八区
会漏掉当天上午的证据（deps.py 注释有完整说明）。
"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kb.graph import KpGraph
from app.models import ExamTemplate, Report, Student
from app.pipeline.attribution import materialize_attribution_verdicts
from app.reports.narrative import render_narrative
from app.reports.quality_analysis import generate_quality_analysis
from app.reports.student_diagnosis import generate_student_diagnosis


def _day_end(d: date | None) -> datetime:
    """本地日末时刻（证据截止）。缺省 = 本地今日末；与 deps._as_dt 同一约定。"""
    cutoff_date = d if d is not None else datetime.now().date()
    return datetime.combine(cutoff_date, time(23, 59))


def _latest_stored_diagnosis(session: Session, student_id: int) -> Report | None:
    """该生最近一场考试的已存诊断（按考试日期降序取最新）。"""
    return session.scalar(
        select(Report)
        .join(ExamTemplate, Report.exam_id == ExamTemplate.id)
        .where(
            Report.student_id == student_id,
            Report.type == "student_diagnosis",
            Report.exam_id.is_not(None),
        )
        .order_by(ExamTemplate.exam_date.desc(), Report.id.desc())
        .limit(1)
    )


def _materialize_verdicts(session: Session, graph: KpGraph, student_id: int, cutoff: datetime) -> None:
    """生成后物化归因裁决行（尾步）。无证据时落 0 行，不抛错。"""
    student = session.get(Student, student_id)
    if student is None:
        raise ValueError(f"学生不存在: {student_id}")
    materialize_attribution_verdicts(session, graph, student_id, student.class_id, cutoff)


def get_or_generate_diagnosis(
    session: Session,
    graph: KpGraph,
    student_id: int,
    *,
    exam_id: int | None = None,
    as_of: date | None = None,
) -> tuple[Report, bool]:
    """该生诊断 get-or-generate 三分支。返回 ``(report, generated)``。

    - ``exam_id``：该场已存诊断优先；无则按考试日 23:59 补生成落库；
    - ``as_of``（无 exam_id）：该时点现算落库；
    - 两者皆无：最近一场考试的已存诊断；无则按本地今日末现算（兼容旧行为）。

    exam_id 不存在时抛 ``ValueError``（路由层翻译 404）。
    """
    if exam_id is not None:
        report = session.scalar(
            select(Report).where(
                Report.exam_id == exam_id,
                Report.type == "student_diagnosis",
                Report.student_id == student_id,
            )
        )
        if report is not None:
            return report, False
        exam = session.get(ExamTemplate, exam_id)
        if exam is None:
            raise ValueError(f"考试不存在: {exam_id}")
        cutoff = datetime.combine(exam.exam_date, time(23, 59))
        report = generate_student_diagnosis(
            session, graph, student_id, as_of=cutoff, narrative=False, exam_id=exam_id
        )
        _materialize_verdicts(session, graph, student_id, cutoff)
        return report, True

    if as_of is None:
        report = _latest_stored_diagnosis(session, student_id)
        if report is not None:
            return report, False
        cutoff = _day_end(None)

        report = generate_student_diagnosis(
            session, graph, student_id, as_of=cutoff, narrative=False
        )
        _materialize_verdicts(session, graph, student_id, cutoff)
        return report, True

    cutoff = _day_end(as_of)
    report = generate_student_diagnosis(
        session, graph, student_id, as_of=cutoff, narrative=False
    )
    _materialize_verdicts(session, graph, student_id, cutoff)
    return report, True


def get_or_generate_quality_report(
    session: Session, graph: KpGraph, class_id: int, exam_id: int
) -> Report:
    """班级质量报告 get-or-generate：已存（提交自动生成）直接返回；无则补生成落库。

    补生成失败抛 ``ValueError``（路由层翻译 400；基础报告为纯计算，正常不会失败）。
    """
    report = session.scalar(
        select(Report).where(
            Report.exam_id == exam_id,
            Report.type == "quality_analysis",
        )
    )
    if report is None:
        try:
            report = generate_quality_analysis(session, graph, class_id, exam_id, narrative=False)
        except Exception as e:  # noqa: BLE001 —— 边界翻译：给调用方统一为领域异常
            raise ValueError(str(e)) from e
    return report


def get_or_create_narrative(session: Session, report: Report) -> str:
    """AI 解读段缓存：首次查看生成并写入 narrative_markdown，之后永久可看。

    仅在 LLM 可用时返回段落；不可用返回空串（调用方按无解读处理）。
    """
    if report.narrative_markdown:
        return report.narrative_markdown

    section = render_narrative(report.content_markdown, report.type)
    if section:
        report.narrative_markdown = section
        session.flush()
        return report.narrative_markdown
    return ""