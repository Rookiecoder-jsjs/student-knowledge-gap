"""班级质量报告：渲染层（架构修复 候选3 第二层）。模型 → markdown，纯字符串拼接。

无 DB、无 LLM：render 只读 ``QualityReportModel``。快照映射集中于此，
与前端契约（``snapshot.stats/question_rates/common_weak``）显式对齐。
"""

from __future__ import annotations

import statistics

from app.config import CLASS_COMMON_WEAK_RATIO
from app.reports.quality_model import QualityReportModel


def render_quality_markdown(model: QualityReportModel) -> str:
    """模型 → markdown（数字全部来自模型，模型数字全部来自计算层）。"""
    lines: list[str] = []
    lines.append(f"# 考后质量分析：{model.exam_name}")
    lines.append("")
    lines.append(
        f"- 班级：{model.class_name}　考试日期：{model.exam_date}　类型：{model.exam_type}"
    )
    lines.append(
        f"- 提交人数：{model.committed} / {model.committed + model.pending}"
        f"（待审核 {model.pending} 人）"
    )
    lines.append(
        f"- 说明：本报告由系统基于已提交成绩自动生成，数字均可追溯至逐题得分；"
        "不含任何排名信息。"
    )
    lines.append("")

    lines.append("## 一、总体情况")
    if model.totals:
        lines.append(
            f"卷面满分 {model.full_total:g} 分；班级平均 {statistics.mean(model.totals):.1f} 分，"
            f"最高 {max(model.totals):g} 分，最低 {min(model.totals):g} 分，"
            f"中位数 {statistics.median(model.totals):.1f} 分"
            + (f"，标准差 {statistics.pstdev(model.totals):.1f}" if len(model.totals) > 1 else "")
            + "。"
        )
    else:
        lines.append("尚无已提交的作答数据。")
    lines.append("")

    lines.append("## 二、逐题得分率")
    lines.append("")
    lines.append("| 题号 | 题型 | 满分 | 得分率 | 涉及知识点 | 状态 |")
    lines.append("|---|---|---|---|---|---|")
    for q in model.question_rates:
        rate_s = f"{q['rate']*100:.0f}%" if q["rate"] is not None else "—"
        status = "⚠️ 低得分率" if q["low"] else ""
        lines.append(
            f"| {q['idx']} | {q['q_type']} | {q['full_score']:g} | {rate_s} | "
            f"{q['kps'] or '未标注'} | {status} |"
        )
    lines.append("")

    lines.append("## 三、知识点班级掌握程度（截至本次考试）")
    lines.append("")
    lines.append("| 知识点 | 有效人数 | 班级平均掌握程度 | 待加强占比 |")
    lines.append("|---|---|---|---|")
    for st in sorted(model.kp_stats.values(), key=lambda d: d["code"]):
        if st["n"] == 0:
            continue
        avg = sum(st["values"]) / len(st["values"])
        lines.append(
            f"| {st['name']}（{st['code']}） | {st['n']} | {avg:.2f} | "
            f"{st['weak']/st['n']*100:.0f}% |"
        )
    lines.append("")

    lines.append("## 四、班级共性待加强点与教学建议")
    lines.append("")
    if model.common_weak:
        lines.append(
            "以下知识点待加强学生占比 ≥ "
            f"{CLASS_COMMON_WEAK_RATIO*100:.0f}%，属班里普遍问题，"
            "建议优先通过集体教学解决（不单独归到学生身上）："
        )
        lines.append("")
        for d in model.common_weak:
            lines.append(
                f"- **{d['name']}**（{d['code']}）：班级平均掌握程度 {d['class_avg']:.2f}，"
                f"待加强占比 {d['weak_share']*100:.0f}%（{d['n']} 人有依据）。"
                "建议：安排重讲或变式训练，下次课用小测复核。"
            )
    else:
        lines.append("未发现达到共性标准的班级待加强点。")
    lines.append("")

    lines.append("## 五、注意事项")
    lines.append("")
    if model.pending > 0:
        lines.append(f"- 仍有 {model.pending} 名学生的作答处于待审核状态，审核后重新生成本报告。")
    untagged = [q["idx"] for q in model.question_rates if not q["kps"]]
    if untagged:
        lines.append(f"- 题目 {untagged} 未标注知识点，不计入知识点分析。")
    lines.append("- 掌握程度按时间远近加权计算，越近的考试越重要；依据少于 3 题的知识点已按「数据不足」处理。")
    lines.append("")
    lines.append("---")
    lines.append("*本报告为初步教学参考；原因分析均需教师确认后使用。*")

    return "\n".join(lines)


def model_to_snapshot(model: QualityReportModel) -> dict:
    """模型 → ``Report.snapshot_json``（前端契约：``stats/question_rates/common_weak``）。"""
    return {
        "class": model.class_name,
        "exam": model.exam_name,
        "exam_date": model.exam_date,
        "committed": model.committed,
        "pending": model.pending,
        "stats": {
            "mean": round(statistics.mean(model.totals), 2) if model.totals else None,
            "max": max(model.totals) if model.totals else None,
            "min": min(model.totals) if model.totals else None,
        },
        "question_rates": model.question_rates,
        "common_weak": model.common_weak,
    }
