"""学习诊断单：渲染层（架构修复 候选3 第二层）。模型 → markdown，纯字符串拼接。

无 DB、无 LLM；``graph`` 仅用于前向影响预警（后代）与可能根源的名称解析。
快照映射集中于此，与前端契约（``snapshot.as_of/weak/attributions``）显式对齐。
"""

from __future__ import annotations

from app.kb.graph import KpGraph
from app.pipeline.weakness import TRAJ_RISING
from app.reports.diagnosis_model import DiagnosisReportModel
from app.reports.labels import attr_label, criterion_label, traj_label

_SUGGESTION = {
    "前置缺陷": "建议先回到根源知识补学，再回到当前内容（顺序比刷题量更重要）。",
    "遗忘衰减": "建议安排 2~3 次间隔复习（今天 / 三天后 / 一周后），用少量题目唤醒即可。",
    "数据不足": "建议布置 3~5 道针对性练习或课堂小测，补足依据后再评估。",
    "易混淆": "建议先用 2~3 道对比题确认是否概念混淆；若确认，用辨析练习区分两个概念的异同。",
}


def render_diagnosis_markdown(graph: KpGraph, model: DiagnosisReportModel) -> str:
    """模型 → markdown（数字全部来自模型，模型数字全部来自计算层）。"""
    lines: list[str] = []
    lines.append(f"# 学习诊断单：{model.student_alias}")
    lines.append("")
    lines.append(
        f"- 班级：{model.class_name}　评估截至：{model.as_of.date()}　"
        "诊断基于已提交的考试/练习记录自动生成。"
    )
    lines.append(
        "- 说明：以下内容是带依据的初步判断，供教师参考与确认；"
        "所有待加强点均标注依据数量与最近表现，教师可否决任何结论。"
    )
    lines.append("")

    # ---- 先呈现进步（成长框架） ----
    lines.append("## 一、保持与进步")
    lines.append("")
    if model.progress:
        for a in model.progress[:8]:
            note = "呈上升趋势" if a.trajectory == TRAJ_RISING else "掌握扎实"
            lines.append(
                f"- **{a.kp_name}**：{note}，当前掌握程度约 "
                f"{(a.mastery or 0)*100:.0f}%（{a.evidence_count} 条依据）。"
            )
    else:
        lines.append("- 暂无足够依据识别明显优势点，随着记录积累将逐步呈现。")
    lines.append("")

    # ---- 薄弱点 + 归因 ----
    lines.append("## 二、下一步需要关注的知识点")
    lines.append("")
    if not model.weak:
        lines.append("- 当前没有达到待加强标准的知识点。")
    for a in model.weak:
        criterion = criterion_label(a.weak_criterion)
        stale_s = "；最近一次依据超过 90 天，情况可能已变化，建议先复测" if a.stale else ""
        lines.append(f"### {a.kp_name}（{a.kp_code}）")
        lines.append(
            f"- 掌握程度约 {(a.mastery or 0)*100:.0f}%（判定：{criterion}），"
            f"依据 {a.evidence_count} 题，变化趋势「{traj_label(a.trajectory)}」{stale_s}"
        )
        if a.per_cog_mastery:
            cog_s = "、".join(
                f"{cog} {m*100:.0f}%" for cog, m in a.per_cog_mastery.items()
            )
            lines.append(
                f"  - 认知层级分层：{cog_s}（如识记与应用差异大，可能是「层级断层」，需针对性补高层级）"
            )
        if a.is_class_common:
            lines.append(
                "- ⚠️ 该点是班里普遍待加强的点，建议主要通过课堂教学调整解决，"
                "不单独归到学生身上。"
            )
        # 前向影响预警（kb-improvement-design K4）：薄弱会波及的后代。
        # 纪律：前向是预测，仅 depth=1 直接后继高置信，depth≥2 标「间接」；措辞「可能波及」非结论。
        desc = graph.descendants(a.kp_id, max_depth=2)
        if desc:
            direct = [graph.kp(did).name for did, d, _ in desc if d == 1]
            indirect = [graph.kp(did).name for did, d, _ in desc if d == 2]
            parts = []
            if direct:
                parts.append("直接可能波及：" + "、".join(direct[:8]))
            if indirect:
                parts.append("间接波及：" + "、".join(indirect[:8]) + "（风险较低）")
            if parts:
                lines.append("- ⚠️ 影响预警：" + "；".join(parts) +
                             "，建议优先补强该地基点，可一并缓解下游。")
        att = model.attributions.get(a.kp_id)
        if att is not None:
            lines.append(
                f"- **可能的原因（初步推测，把握 {att.confidence*100:.0f}%）**：{attr_label(att.type)}"
            )
            if att.root_kp_id is not None:
                root = graph.kp(att.root_kp_id)
                lines.append(f"  - 可能根源：{root.name}（{root.code}）")
            if att.evidence:
                ev_s = "；".join(
                    f"{e.get('ancestor', '')} 掌握程度 {e.get('mastery')}"
                    for e in att.evidence
                    if isinstance(e, dict) and "ancestor" in e
                )
                if ev_s:
                    lines.append(f"  - 依据：{ev_s}")
            lines.append(f"  - 验证方式：{att.prediction}")
            lines.append(f"  - 建议：{_SUGGESTION.get(att.type, '建议教师结合课堂观察研判。')}")
        else:
            lines.append("- 可能的原因：暂未匹配到规则成因，建议教师结合课堂观察研判。")
        lines.append("")

    # ---- 数据不足与未学到 ----
    if model.insufficient:
        lines.append("## 三、依据不足，暂不判断")
        lines.append("")
        for a in model.insufficient[:10]:
            lines.append(
                f"- {a.kp_name}：仅 {a.evidence_count} 条依据（门槛 3 条），"
                "系统不做待加强判定，避免误判。"
            )
        lines.append("")
    if model.not_learned:
        lines.append("## 四、尚未学到（按教学进度）")
        lines.append("")
        lines.append(
            "以下知识点教学进度尚未覆盖，任何相关失分都不计入待加强判定："
            + "、".join(a.kp_name for a in model.not_learned[:12])
            + ("等" if len(model.not_learned) > 12 else "")
            + "。"
        )
        lines.append("")

    lines.append("---")
    lines.append(
        "*本诊断单仅覆盖知识维度；学习动机、情绪与家庭因素不在系统判断范围内。*"
    )
    return "\n".join(lines)


def model_to_snapshot(model: DiagnosisReportModel) -> dict:
    """模型 → ``Report.snapshot_json``（前端契约：``as_of/weak/attributions``）。"""
    return {
        "student_alias": model.student_alias,
        "class": model.class_name,
        "as_of": str(model.as_of.date()),
        "weak": [
            {
                "kp": a.kp_code,
                "mastery": round(a.mastery or 0, 3),
                "criterion": a.weak_criterion,
                "evidence_count": a.evidence_count,
                "trajectory": a.trajectory,
                "class_common": a.is_class_common,
            }
            for a in model.weak
        ],
        "attributions": [
            {"kp_id": att.kp_id, "type": att.type, "confidence": att.confidence}
            for att in model.attributions.values()
        ],
    }
