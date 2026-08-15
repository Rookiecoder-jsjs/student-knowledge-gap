"""学习诊断单：纯计算模型（架构修复 候选3 分层第一层）。

compute / render / persist 三层；诊断的归因段来自 ``resolve_attributions``
（候选1 derive-on-read，overridden 在此过滤为不可见）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from app.kb.graph import KpGraph
from app.models import Class, EvidenceEvent, Student
from app.pipeline.attribution import ResolvedAttribution, resolve_attributions
from app.pipeline.weakness import (
    GATE_INSUFFICIENT,
    GATE_NOT_LEARNED,
    KpAssessment,
    TRAJ_RISING,
    assess_student_kps,
)


@dataclass
class DiagnosisReportModel:
    """学习诊断单的全部结构（纯数据，可快照、可渲染）。"""

    student_alias: str
    class_name: str
    class_id: int
    as_of: datetime
    weak: list[KpAssessment] = field(default_factory=list)
    progress: list[KpAssessment] = field(default_factory=list)
    not_learned: list[KpAssessment] = field(default_factory=list)
    insufficient: list[KpAssessment] = field(default_factory=list)
    # kp_id -> active 归因（仅 verdict=active；被否决的假设不作「可能的原因」呈现）
    attributions: dict[int, ResolvedAttribution] = field(default_factory=dict)


def compute_diagnosis_model(
    session: Session,
    graph: KpGraph,
    student_id: int,
    as_of: datetime | None = None,
    *,
    assessments: list[KpAssessment] | None = None,
    events_by_sk: dict[tuple[int, int], list[EvidenceEvent]] | None = None,
) -> DiagnosisReportModel:
    """纯计算：评估 + 归因解析 + 弱项/进步/未学/数据不足分类（无 markdown、无落库）。"""
    student = session.get(Student, student_id)
    clazz = session.get(Class, student.class_id)
    if as_of is None:
        # 本地时刻：utcnow 作证据截止在东八区会漏掉当天上午的证据（deps._as_dt 同一约定）
        as_of = datetime.now()

    # events_by_sk 由调用方批量预取传入（提交自动生成时全班共享一次扫描），缺省则内部各取。
    # assessments 可复用（候选1：诊断与物化共享一次评估），缺省则内部评估。
    if assessments is None:
        assessments = assess_student_kps(
            session, graph, student_id, student.class_id, as_of, events_by_sk=events_by_sk
        )
    # 归因 derive-on-read（候选1）：不再读 Attribution 表 active 缓存行、不依赖外部
    # 「打底」。overridden（教师否决/诊断题证伪）过滤为不可见——与旧行为一致（不变量②）。
    attributions = {
        r.kp_id: r
        for r in resolve_attributions(
            session,
            graph,
            student_id,
            student.class_id,
            as_of,
            assessments=assessments,
            events_by_sk=events_by_sk,
        )
        if r.verdict == "active"
    }

    covered_valid = [a for a in assessments if a.gate is None]
    weak = [a for a in covered_valid if a.is_weak]
    # 报告排序（kb-improvement-design K5）：基础 > 核心 > 拓展，同级别按掌握度缺口降序。
    # 教师应先补地基点；"科学记数法"（拓展）的薄弱不与"绝对值"（基础）同级竞争注意力。
    _IMP_RANK = {"基础": 0, "核心": 1, "拓展": 2}
    weak.sort(
        key=lambda a: (
            _IMP_RANK.get(graph.kp(a.kp_id).importance, 1),
            -(1.0 - (a.mastery if a.mastery is not None else 1.0)),
        )
    )
    progress = [
        a for a in covered_valid if a.trajectory == TRAJ_RISING or (a.mastery or 0) >= 0.85
    ]
    not_learned = [a for a in assessments if a.gate == GATE_NOT_LEARNED]
    insufficient = [a for a in assessments if a.gate == GATE_INSUFFICIENT]

    return DiagnosisReportModel(
        student_alias=student.name_or_alias,
        class_name=clazz.name,
        class_id=student.class_id,
        as_of=as_of,
        weak=weak,
        progress=progress,
        not_learned=not_learned,
        insufficient=insufficient,
        attributions=attributions,
    )
