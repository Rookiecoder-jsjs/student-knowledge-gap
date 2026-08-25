"""班级诊断单聚合查询（diagnosis-sheet-redesign.md §1.2/§3.1-B1）。

「班级现状」是跨考试滚动统计（考试是采集事件，诊断是持续状态）：
- 数据截至 = 该班最近一场已提交考试的日期；
- 全班待加强 K 点数 / 班级共性 M 点：以截至时点 derive-on-read 现算；
- 近两场提升/回落计数：最近两场已提交考试各自共性薄弱点集合的进出。

纯查询+推导，不落库；行动明细/闭环条由 intervention-loop 计算层提供
（app/intervention.py，derive-on-read 同纪律）。
"""

from __future__ import annotations

import statistics
from datetime import datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import CLASS_COMMON_WEAK_RATIO
from app.kb.graph import KpGraph
from app.models import ExamResponse, ExamTemplate, Student
from app.pipeline.mastery import get_events_batch
from app.pipeline.weakness import assess_student_kps


def _common_weak_of_exam(
    session: Session,
    graph: KpGraph,
    class_id: int,
    exam: ExamTemplate,
) -> tuple[list[str], float | None]:
    """一场考试的 (共性薄弱 kp 名列表, 平均得分率)。"""
    as_of = datetime.combine(exam.exam_date, time(23, 59))
    student_ids = list(
        session.scalars(
            select(Student.id).where(Student.class_id == class_id)
        )
    )
    committed = list(
        session.scalars(
            select(ExamResponse.student_id).where(
                ExamResponse.exam_template_id == exam.id,
                ExamResponse.status == "已提交",
            )
        )
    )
    if not committed:
        return [], None
    events_by_sk = get_events_batch(session, student_ids, list(graph.grade7_kp_ids()), as_of)
    weak_count: dict[int, int] = {}
    n_assessed: dict[int, int] = {}
    for sid in committed:
        for a in assess_student_kps(
            session, graph, sid, class_id, as_of, events_by_sk=events_by_sk
        ):
            if a.gate is not None or a.mastery is None:
                continue
            n_assessed[a.kp_id] = n_assessed.get(a.kp_id, 0) + 1
            if a.is_weak:
                weak_count[a.kp_id] = weak_count.get(a.kp_id, 0) + 1
    common = [
        graph.kp(kp_id).name
        for kp_id, c in weak_count.items()
        if n_assessed.get(kp_id, 0) >= 4 and c / n_assessed[kp_id] >= CLASS_COMMON_WEAK_RATIO
    ]
    # 平均得分率：该场质量报告快照若有则复用（省一次计算），否则现算留 None 由调用方补
    return sorted(common), None


def class_diagnosis_sheet(
    session: Session, graph: KpGraph, class_id: int
) -> dict:
    """班级诊断单聚合（B1）。滚动统计 + 最新改进意见 + 行动/闭环摘要。"""
    from app.models import Report

    students_n = session.scalar(
        select(func.count()).select_from(Student).where(Student.class_id == class_id)
    )

    # 已提交过作答的考试（滚动窗口），按日期降序
    exams = list(
        session.scalars(
            select(ExamTemplate)
            .where(
                ExamTemplate.class_id == class_id,
                ExamTemplate.id.in_(
                    select(ExamResponse.exam_template_id).where(
                        ExamResponse.status == "已提交"
                    )
                ),
            )
            .order_by(ExamTemplate.exam_date.desc(), ExamTemplate.id.desc())
        )
    )

    latest_advice = session.scalar(
        select(Report)
        .join(ExamTemplate, Report.exam_id == ExamTemplate.id)
        .where(
            Report.class_id == class_id,
            Report.type == "class_improvement_advice",
        )
        .order_by(ExamTemplate.exam_date.desc(), Report.id.desc())
        .limit(1)
    ) if exams else session.scalar(
        select(Report)
        .where(
            Report.class_id == class_id,
            Report.type == "class_improvement_advice",
        )
        .order_by(Report.generated_at.desc(), Report.id.desc())
        .limit(1)
    )

    # ---- 班级现状：数据截至 + 待加强/共性强度的滚动口径 ----
    status = {
        "student_count": students_n or 0,
        "exam_count": len(exams),
        "data_as_of": str(exams[0].exam_date) if exams else None,
        "weak_kp_total": 0,
        "common_weak": [],
        "trend": {"entered": 0, "exited": 0, "prev_exam": None},
    }

    if exams:
        latest = exams[0]
        as_of = datetime.combine(latest.exam_date, time(23, 59))
        student_ids = [
            sid for (sid,) in session.execute(
                select(Student.id).where(Student.class_id == class_id)
            ).all()
        ]
        events_by_sk = get_events_batch(
            session, student_ids, list(graph.grade7_kp_ids()), as_of
        )
        weak_kp_ids: set[int] = set()
        weak_count: dict[int, int] = {}
        n_assessed: dict[int, int] = {}
        per_class_common: dict[int, dict] = {}
        for sid in [s for s in student_ids]:
            resp = session.scalar(
                select(ExamResponse.id).where(
                    ExamResponse.exam_template_id == latest.id,
                    ExamResponse.student_id == sid,
                    ExamResponse.status == "已提交",
                )
            )
            if resp is None:
                continue
            for a in assess_student_kps(
                session, graph, sid, class_id, as_of, events_by_sk=events_by_sk
            ):
                if a.gate is not None or a.mastery is None:
                    continue
                weak_kp_ids.add(a.kp_id)
                n_assessed[a.kp_id] = n_assessed.get(a.kp_id, 0) + 1
                if a.is_weak:
                    weak_count[a.kp_id] = weak_count.get(a.kp_id, 0) + 1
                    st = per_class_common.setdefault(
                        a.kp_id, {"name": a.kp_name, "values": [], "n": 0}
                    )
                    st["values"].append(a.mastery)

        status["weak_kp_total"] = len(weak_kp_ids)
        common: list[dict] = []
        for kp_id, st in per_class_common.items():
            share = weak_count.get(kp_id, 0) / max(n_assessed.get(kp_id, 1), 1)
            if n_assessed.get(kp_id, 0) >= 4 and share >= CLASS_COMMON_WEAK_RATIO:
                common.append(
                    {
                        "kp": st["name"],
                        "weak_share_pct": round(share * 100),
                        "class_avg_mastery_pct": round(
                            statistics.mean(st["values"]) * 100
                        ),
                    }
                )
        common.sort(key=lambda d: d["class_avg_mastery_pct"])
        status["common_weak"] = common[:5]

        # 近两场趋势：上一场 vs 本场的共性薄弱点集合进出
        if len(exams) >= 2:
            prev_names, _ = _common_weak_of_exam(session, graph, class_id, exams[1])
            curr_names = {d["kp"] for d in status["common_weak"]}
            prev_set = set(prev_names)
            status["trend"] = {
                "prev_exam": exams[1].name,
                "entered": sorted(curr_names - prev_set),
                "exited": sorted(prev_set - curr_names),
            }

    advice_payload = None
    if latest_advice is not None:
        advice_payload = {
            "report_id": latest_advice.id,
            "markdown": latest_advice.content_markdown,
            "generated_at": (
                latest_advice.generated_at.isoformat()
                if latest_advice.generated_at
                else None
            ),
            "writer": (latest_advice.snapshot_json or {}).get("writer"),
            "exam_id": latest_advice.exam_id,
        }

    # ---- 行动明细 + 闭环条（intervention-loop-design 落地接入，替换原空占位） ----
    from app.intervention import action_plan_view, intervention_summary

    plan = action_plan_view(session, graph, class_id)
    summary = intervention_summary(session, graph, class_id)

    return {
        "class_id": class_id,
        "status": status,
        "improvement_advice": advice_payload,
        # 行动明细：pending_confirm 计数 + 三层全量行（唯一全量版面，§1.2）
        "actions": {
            "pending_confirm": plan["pending_confirm"],
            "rows": plan["rows"],
        },
        # 闭环摘要：采纳率 / 干预提升率 / 待复测分布
        "intervention_summary": summary,
        "past_exams": [
            {
                "exam_id": e.id,
                "name": e.name,
                "exam_date": str(e.exam_date),
                "type": e.type,
            }
            for e in exams[:12]
        ],
    }
