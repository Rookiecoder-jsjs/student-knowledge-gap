"""学生改进单（intervention-loop-design.md §2）：教师代发的学生侧行动卡。

新报告类型 ``student_action_plan``：随提交与诊断单一起自动生成落库
（auto_generate 幂等替换）。学生视角与诊断单的关键差异：

- 成长框架先行（先进步的放最前）；改进项 ≤3（认知负荷控制）；
- 禁负面定性词（「下一步」「待加强」替代「薄弱」）、无排名、无他人/班级数据；
- 学法建议由命中的干预策略确定性推出（知识维度内），无归因匹配只写
  「找老师聊聊这个点」，不编造。

LLM 生成层：模板渲染落库后追加 LLM 升级步骤（plan_writer.write_student_action_plan），
成功替换正文 + writer 溯源；失败/熔断/开关关闭则模板版即终版。best-effort 不变。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.kb.graph import KpGraph
from app.llm.plan_writer import write_student_action_plan
from app.models import EvidenceEvent, Report
from app.pipeline.weakness import TRAJ_RISING, KpAssessment
from app.reports.diagnosis_model import (
    DiagnosisReportModel,
    compute_diagnosis_model,
)
from app.reports.diagnosis_render import model_to_snapshot

_PROMPT_VERSION = "plan-writer-v0.1.0"

# 学生版策略句（kind → 怎么做/什么时候；与 intervention KIND_* 对应）
_PLAN_STRATEGY = {
    "prereq_backfill": ("先回头把基础点补上，再回到现在的内容——顺序比刷题量更重要", "本周内"),
    "spaced_review": ("当天花 5 分钟回想一遍学过的内容，比考前突击有用得多", "今天起连续一周"),
    "contrast_practice": ("把容易混的两个概念写在一起，先说出它们哪里不一样，再做题", "本周内"),
    "tier_drill": ("每学一个结论就问自己「什么时候用它」，多练变式与应用题", "本周内"),
    "evidence_boost": ("做几道小题让老师看看你的真实水平", "本周内"),
}


def _student_friendly_strategy(att_type: str | None) -> tuple[str, str]:
    """归因类型（未命中干预行时）→ 学生版策略句（保底路径用）。"""
    return {
        "前置缺陷": _PLAN_STRATEGY["prereq_backfill"],
        "遗忘衰减": _PLAN_STRATEGY["spaced_review"],
        "易混淆": _PLAN_STRATEGY["contrast_practice"],
        "数据不足": _PLAN_STRATEGY["evidence_boost"],
    }.get(att_type, ("和老师一起看看这个点卡在哪里", "本周内"))


def render_action_plan_markdown(graph: KpGraph, model: DiagnosisReportModel) -> str:
    """模型 → 行动卡 markdown（数字全部来自模型；学生视角措辞纪律硬约束）。"""
    lines: list[str] = []
    lines.append(f"# {model.student_alias} 的下一步行动卡")
    lines.append("")
    lines.append(f"- 评估截至：{model.as_of.date()}　由老师根据你的学习记录生成")
    lines.append("")

    lines.append("### 你已经在进步的")
    lines.append("")
    if model.progress:
        for a in model.progress[:5]:
            note = "最近比之前好了不少，保持这个节奏" if a.trajectory == TRAJ_RISING else "掌握得比较扎实"
            lines.append(f"- {a.kp_name}：{note}。")
    else:
        lines.append("- 这一阶段先把基础打稳，进步会一点点看得见。")
    lines.append("")

    lines.append("### 你的下一步")
    lines.append("")
    if not model.weak:
        lines.append("- 目前没有需要特别加强的点，按现有节奏继续。")
    for a in model.weak[:3]:  # 改进项上限 3 条，其余进教师诊断单
        att = model.attributions.get(a.kp_id)
        how, when = _student_friendly_strategy(att.type if att else None)
        mastery_pct = round((a.mastery or 0) * 100)
        stuck = "基础点需要先补" if (att and att.type == "前置缺陷") else "需要多练几道变式"
        lines.append(f"### {a.kp_name}")
        lines.append(f"- 现状：目前掌握约 {mastery_pct}%，主要卡在{stuck}上。")
        lines.append(f"- 怎么做：{how}。")
        lines.append(f"- 什么时候：{when}。做完可以找老师要 2~3 道小题自查。")
    if len(model.weak) > 3:
        lines.append("")
        lines.append(f"- 还有 {len(model.weak) - 3} 个点老师会在课上单独帮你安排。")
    lines.append("")

    lines.append("### 学习方法小建议")
    lines.append("")
    seen: set[str] = set()
    for a in model.weak:
        att = model.attributions.get(a.kp_id)
        t = att.type if att else None
        tip = {
            "遗忘衰减": "学过的东西会忘是正常的——当天花几分钟回想一遍，记得更牢",
            "前置缺陷": "卡住你的不是现在的内容，是前面一个基础点；先补上它，新课会突然变容易",
            "易混淆": "把容易混的概念写在一起对比，先说不同再做题",
            None: None,
        }.get(t)
        if tip and t not in seen:
            lines.append(f"- {tip}。")
            seen.add(t)
    if not seen:
        lines.append("- 做题前先想「这题考的是哪个点」，带着问题听课效率更高。")
    lines.append("")
    lines.append("---")
    lines.append("*这张行动卡由系统根据你的学习记录整理，有不确定的地方老师会和你当面聊。*")
    return "\n".join(lines)


def generate_student_action_plan(
    session: Session,
    graph: KpGraph,
    student_id: int,
    as_of: datetime | None = None,
    assessments: list[KpAssessment] | None = None,
    events_by_sk: dict[tuple[int, int], list[EvidenceEvent]] | None = None,
    exam_id: int | None = None,
) -> Report:
    """生成并落库一份学生改进单（幂等替换由 auto_generate 负责）。"""
    model = compute_diagnosis_model(
        session, graph, student_id, as_of,
        assessments=assessments, events_by_sk=events_by_sk,
    )
    markdown = render_action_plan_markdown(graph, model)

    report = Report(
        type="student_action_plan",
        class_id=model.class_id,
        student_id=student_id,
        exam_id=exam_id,
        snapshot_json=model_to_snapshot(model),
        content_markdown=markdown,
    )
    session.add(report)
    session.flush()

    # LLM 升级（best-effort）：成功替换正文 + writer 溯源；失败保留模板版
    try:
        draft = write_student_action_plan(graph, model)
    except Exception:  # noqa: BLE001 —— 生成层自身抛错也降级为模板
        draft = None
    if draft is not None:
        report.content_markdown = draft.markdown
        snapshot = dict(report.snapshot_json or {})
        snapshot["writer"] = {"model": draft.model, "prompt_version": _PROMPT_VERSION}
        report.snapshot_json = snapshot
    session.flush()
    return report
