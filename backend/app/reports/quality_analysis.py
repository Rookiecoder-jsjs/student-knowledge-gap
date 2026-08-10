"""一键考后质量分析文档（DESIGN §9 第一交付物，班级层）。

架构修复 候选3 分层：compute（``quality_model``）→ render（``quality_render``）
→ persist（本模块）。本文件只做组合与落库，不再内嵌统计/渲染。
内容：总体情况（不排名）→ 逐题得分率 → 知识点班级掌握度 →
班级共性薄弱点与教学建议 → 异常波动提醒。数字全部系统注入。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.kb.graph import KpGraph
from app.llm.gateway import narrate
from app.models import EvidenceEvent, Report
from app.reports.quality_model import compute_quality_model
from app.reports.quality_render import model_to_snapshot, render_quality_markdown


def generate_quality_analysis(
    session: Session,
    graph: KpGraph,
    class_id: int,
    exam_id: int,
    narrative: bool = False,
    events_by_sk: dict[tuple[int, int], list[EvidenceEvent]] | None = None,
) -> Report:
    model = compute_quality_model(session, graph, class_id, exam_id, events_by_sk=events_by_sk)
    markdown = render_quality_markdown(model)
    if narrative:
        section = narrate(markdown, "quality_analysis")
        if section:
            markdown += section

    report = Report(
        type="quality_analysis",
        class_id=class_id,
        exam_id=exam_id,  # 关联到具体考试（提交后自动生成 / get-or-generate 落库）
        snapshot_json=model_to_snapshot(model),
        content_markdown=markdown,
    )
    session.add(report)
    session.flush()
    return report
