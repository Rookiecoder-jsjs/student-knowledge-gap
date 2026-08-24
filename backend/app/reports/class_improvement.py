"""班级改进意见（diagnosis-sheet-redesign.md §1.2/§2.4）：教师视角「接下来先做什么」。

新报告类型 ``class_improvement_advice``：每场提交随质量报告一起生成一份，
班级诊断单读取该班**最新**一份（滚动，跨考试聚合的「持续状态」语义）。

- 模板保底：由共性薄弱点合成 3~5 条建议（全班重讲 → 小组 → 个体汇总）；
- LLM 升级：开关开启时经 plan_writer 重写正文，snapshot_json 记 writer 溯源。
  校验纪律：每条须能对上证据包中的知识点名、不得出现学生姓名（forbidden_names
  由调用方传名单兜底）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.kb.graph import KpGraph
from app.llm.plan_writer import write_class_advice
from app.models import Report
from app.reports.quality_model import QualityReportModel, compute_quality_model

_PROMPT_VERSION = "plan-writer-v0.1.0"


def _template_markdown(quality: QualityReportModel) -> str:
    """模板保底正文：从共性薄弱点/低得分率题合成 3~5 条建议。"""
    lines: list[str] = ["# 班级改进意见", ""]
    lines.append(f"- 考试：{quality.exam_name}（{quality.exam_date}）　"
                 f"提交 {quality.committed}/{quality.committed + quality.pending}")
    lines.append("")
    lines.append("## 建议按顺序先做这几件事")
    lines.append("")

    items: list[str] = []
    for d in quality.common_weak[:3]:
        items.append(
            f"- **{d['name']}** 全班重讲：该点 {d['n']} 人中约 "
            f"{round(d['weak_share']*100)}% 待加强（班级均掌握约 "
            f"{round(d['class_avg']*100)}%），本周内安排一次讲评课集中处理。"
        )
    low_qs = [q for q in quality.question_rates if q.get("low")]
    if low_qs and len(items) < 4:
        q = low_qs[0]
        kps = q["kps"] or "对应知识点"
        items.append(
            f"- **第 {q['idx']} 题错因讲评**（{kps}）：本题得分率不足 60%，"
            "讲评后布置 2~3 道同型变式题当堂检验。"
        )
    if len(items) < 3:
        items.append(
            "- 整体表现平稳：维持现有教学节奏，下次考试前安排一次综合小测查漏。"
        )
    items.append(
        "- 共性点讲评后仍薄弱的学生汇总给任课教师，指向各学生的改进单跟进；"
        "建议下次考试前完成一轮针对性练习。"
    )

    lines.extend(items[:5])
    lines.append("")
    lines.append("---")
    lines.append("*以上为系统基于班级聚合数据生成的建议，供教师参考调整。*")
    return "\n".join(lines)


def generate_class_improvement_advice(
    session: Session,
    graph: KpGraph,
    class_id: int,
    exam_id: int,
    as_of: datetime | None = None,
    events_by_sk=None,
    forbidden_names: list[str] | None = None,
) -> Report:
    """生成并落库一份班级改进意见（每场考试一份；幂等替换由 auto_generate 负责）。"""
    quality = compute_quality_model(
        session, graph, class_id, exam_id, events_by_sk=events_by_sk
    )
    markdown = _template_markdown(quality)

    report = Report(
        type="class_improvement_advice",
        class_id=class_id,
        exam_id=exam_id,
        snapshot_json={
            "exam": quality.exam_name,
            "as_of": str(as_of.date()) if as_of else quality.exam_date,
            "common_weak": [
                {"kp": d["name"], "weak_share_pct": round(d["weak_share"] * 100)}
                for d in quality.common_weak[:5]
            ],
        },
        content_markdown=markdown,
    )
    session.add(report)
    session.flush()

    # LLM 升级（best-effort）：成功替换正文 + writer 溯源；失败保留模板版
    try:
        draft = write_class_advice(
            graph, quality, as_of=as_of, forbidden_names=forbidden_names
        )
    except Exception:  # noqa: BLE001 —— 生成层自身抛错也降级为模板
        draft = None
    if draft is not None:
        report.content_markdown = draft.markdown
        snapshot = dict(report.snapshot_json or {})
        snapshot["writer"] = {"model": draft.model, "prompt_version": _PROMPT_VERSION}
        report.snapshot_json = snapshot
    session.flush()
    return report
