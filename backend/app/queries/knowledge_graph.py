"""知识结构图聚合查询。

图谱的结构来自知识库版本，班级指标则按截至时点从证据事件派生。
这里把班级维度一次性聚合，避免前端为每个学生/知识点发起 N+1 请求。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kb.graph import KpGraph
from app.models import KpRelation, Student
from app.pipeline.mastery import get_events_batch
from app.pipeline.weakness import assess_student_kps


def class_knowledge_graph(
    session: Session,
    graph: KpGraph,
    class_id: int,
    as_of: datetime,
    class_grade: int | None = None,
) -> dict:
    """返回班级可见知识图谱及按知识点聚合的掌握度指标。

    薄弱判定复用 ``assess_student_kps``，确保图谱颜色与诊断单使用同一套
    门槛、底线和班级 P25 规则；结构节点仍保留暂无证据的知识点，便于老师
    识别尚未教学或数据不足的区域。
    """

    student_ids = list(
        session.scalars(select(Student.id).where(Student.class_id == class_id))
    )
    metric_kp_ids = list(
        graph.grade_kp_ids(class_grade) if class_grade is not None else graph.grade7_kp_ids()
    )
    events_by_sk = get_events_batch(session, student_ids, metric_kp_ids, as_of)

    mastery_sum: defaultdict[int, float] = defaultdict(float)
    mastery_count: defaultdict[int, int] = defaultdict(int)
    weak_count: defaultdict[int, int] = defaultdict(int)
    evidence_count: defaultdict[int, int] = defaultdict(int)

    for student_id in student_ids:
        assessments = assess_student_kps(
            session,
            graph,
            student_id,
            class_id,
            as_of,
            events_by_sk=events_by_sk,
        )
        for assessment in assessments:
            if assessment.gate is not None or assessment.mastery is None:
                continue
            mastery_sum[assessment.kp_id] += assessment.mastery
            mastery_count[assessment.kp_id] += 1
            evidence_count[assessment.kp_id] += assessment.evidence_count
            if assessment.is_weak:
                weak_count[assessment.kp_id] += 1

    node_ids = {
        kp_id
        for kp_id in graph.kp_ids()
        if not getattr(graph.kp(kp_id), "archived", False)
        and (class_grade is None or graph.kp(kp_id).grade == class_grade)
    }
    nodes: list[dict] = []
    for kp_id in sorted(node_ids, key=lambda value: graph.kp(value).code):
        kp = graph.kp(kp_id)
        count = mastery_count.get(kp_id, 0)
        nodes.append(
            {
                "id": kp.id,
                "code": kp.code,
                "name": kp.name,
                "chapter": kp.chapter or "未分组",
                "grade": kp.grade,
                "importance": kp.importance,
                "mastery": round(mastery_sum[kp_id] / count, 3) if count else None,
                "weak_share": round(weak_count[kp_id] / count, 3) if count else None,
                "evidence_count": evidence_count.get(kp_id, 0),
                "student_count": count,
            }
        )

    return {
        "kb_version_id": graph.kb_version_id,
        "as_of": as_of.date().isoformat(),
        "nodes": nodes,
        "edges": _relations(session, node_ids),
    }


def student_knowledge_graph(
    session: Session,
    graph: KpGraph,
    student_id: int,
    class_id: int,
    as_of: datetime,
    class_grade: int | None = None,
) -> dict:
    """返回学生自服务图谱，只携带该生自己的指标。"""

    metric_kp_ids = list(
        graph.grade_kp_ids(class_grade) if class_grade is not None else graph.grade7_kp_ids()
    )
    events_by_sk = get_events_batch(session, [student_id], metric_kp_ids, as_of)
    assessments = {
        assessment.kp_id: assessment
        for assessment in assess_student_kps(
            session,
            graph,
            student_id,
            class_id,
            as_of,
            events_by_sk=events_by_sk,
        )
    }

    node_ids = {
        kp_id
        for kp_id in graph.kp_ids()
        if not getattr(graph.kp(kp_id), "archived", False)
        and (class_grade is None or graph.kp(kp_id).grade == class_grade)
    }
    nodes: list[dict] = []
    for kp_id in sorted(node_ids, key=lambda value: graph.kp(value).code):
        kp = graph.kp(kp_id)
        assessment = assessments.get(kp_id)
        if assessment is None or assessment.gate == "未学到":
            state = "not_learned" if assessment and assessment.gate == "未学到" else "no_data"
        elif assessment.gate is not None or assessment.mastery is None:
            state = "no_data"
        elif assessment.is_weak:
            state = "weak"
        elif assessment.mastery < 0.8:
            state = "watch"
        else:
            state = "good"
        nodes.append(
            {
                "id": kp.id,
                "code": kp.code,
                "name": kp.name,
                "chapter": kp.chapter or "未分组",
                "grade": kp.grade,
                "importance": kp.importance,
                "mastery": (
                    round(assessment.mastery, 3)
                    if assessment and assessment.mastery is not None
                    else None
                ),
                "weak_share": None,
                "evidence_count": assessment.evidence_count if assessment else 0,
                "student_count": 1 if assessment and assessment.mastery is not None else 0,
                "state": state,
            }
        )

    return {
        "kb_version_id": graph.kb_version_id,
        "as_of": as_of.date().isoformat(),
        "nodes": nodes,
        "edges": _relations(session, node_ids),
    }


def _relations(session: Session, node_ids: set[int]) -> list[dict]:
    if not node_ids:
        return []
    rows = session.scalars(
        select(KpRelation)
        .where(
            KpRelation.from_kp_id.in_(node_ids),
            KpRelation.to_kp_id.in_(node_ids),
        )
        .order_by(KpRelation.id)
    )
    return [
        {
            "id": relation.id,
            "from": relation.from_kp_id,
            "to": relation.to_kp_id,
            "type": relation.type,
            "weight": relation.weight,
        }
        for relation in rows
    ]
