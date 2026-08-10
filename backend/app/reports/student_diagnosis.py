"""个人诊断单（DESIGN §9）：薄弱点 + 证据 + 归因假设 + 下一步建议。

架构修复 候选3 分层：compute（``diagnosis_model``，含候选1 的 derive-on-read 归因）
→ render（``diagnosis_render``）→ persist（本模块）。

呈现伦理：成长框架（先进步、后缺口，缺口表述为"下一步"），
无绝对化措辞，无排名；归因一律标注为"待教师确认的假设"。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.kb.graph import KpGraph
from app.llm.gateway import narrate
from app.models import EvidenceEvent, Report
from app.pipeline.weakness import KpAssessment
from app.reports.diagnosis_model import compute_diagnosis_model
from app.reports.diagnosis_render import model_to_snapshot, render_diagnosis_markdown


def generate_student_diagnosis(
    session: Session,
    graph: KpGraph,
    student_id: int,
    as_of: datetime | None = None,
    narrative: bool = False,
    assessments: list[KpAssessment] | None = None,
    events_by_sk: dict[tuple[int, int], list[EvidenceEvent]] | None = None,
    exam_id: int | None = None,
) -> Report:
    model = compute_diagnosis_model(
        session, graph, student_id, as_of,
        assessments=assessments, events_by_sk=events_by_sk,
    )
    markdown = render_diagnosis_markdown(graph, model)
    if narrative:
        section = narrate(markdown, "student_diagnosis")
        if section:
            markdown += section

    report = Report(
        type="student_diagnosis",
        class_id=model.class_id,
        student_id=student_id,
        exam_id=exam_id,  # 关联到具体考试（提交自动生成 / get-or-generate 落库）
        snapshot_json=model_to_snapshot(model),
        content_markdown=markdown,
    )
    session.add(report)
    session.flush()
    return report
