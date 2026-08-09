"""一键考后质量分析文档（DESIGN §9 第一交付物，班级层）。

内容：总体情况（不排名）→ 逐题得分率 → 知识点班级掌握度 →
班级共性薄弱点与教学建议 → 异常波动提醒。
数字全部系统注入；生成时物化快照到 report 表。
"""

from __future__ import annotations

import statistics
from datetime import datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import CLASS_COMMON_WEAK_RATIO
from app.kb.graph import KpGraph
from app.models import (
    Class,
    EvidenceEvent,
    ExamResponse,
    ExamTemplate,
    Report,
    ResponseAnswer,
    Student,
    TemplateQuestion,
)
from app.pipeline.mastery import get_events_batch
from app.pipeline.weakness import KpAssessment, assess_student_kps


def generate_quality_analysis(
    session: Session,
    graph: KpGraph,
    class_id: int,
    exam_id: int,
    narrative: bool = False,
    events_by_sk: dict[tuple[int, int], list[EvidenceEvent]] | None = None,
) -> Report:
    clazz = session.get(Class, class_id)
    template = session.get(ExamTemplate, exam_id)
    as_of = datetime.combine(template.exam_date, time(23, 59))

    students = list(
        session.scalars(select(Student).where(Student.class_id == class_id))
    )
    committed = {
        r.student_id: r
        for r in session.scalars(
            select(ExamResponse).where(
                ExamResponse.exam_template_id == exam_id,
                ExamResponse.status == "已提交",
            )
        )
    }
    pending = len(students) - len(committed)

    totals = [r.total_score for r in committed.values()]
    questions = list(
        session.scalars(
            select(TemplateQuestion)
            .where(TemplateQuestion.exam_template_id == exam_id)
            .order_by(TemplateQuestion.idx)
        )
    )
    full_total = sum(q.full_score for q in questions)

    # ---- 逐题得分率 ----
    q_rates: list[dict] = []
    for q in questions:
        rates = []
        for r in committed.values():
            ans = session.scalar(
                select(ResponseAnswer).where(
                    ResponseAnswer.exam_response_id == r.id,
                    ResponseAnswer.template_question_id == q.id,
                )
            )
            if ans is not None and q.full_score > 0:
                rates.append(ans.score / q.full_score)
        rate = sum(rates) / len(rates) if rates else None
        kp_names = ", ".join(graph.kp(qk.kp_id).name for qk in q.kps)
        q_rates.append(
            {
                "idx": q.idx,
                "q_type": q.q_type,
                "full_score": q.full_score,
                "rate": rate,
                "kps": kp_names,
                "low": rate is not None and rate < 0.6,
            }
        )

    # ---- 班级知识点掌握度（derive-on-read） ----
    # G4：一次预取全班×全 kp 证据，跨学生复用（原逐学生 assess 内部各预取一次 = 重复全表扫描）
    class_student_ids = [s.id for s in students]
    # G4：一次预取全班×全 kp 证据，跨学生复用。events_by_sk 由调用方预取传入可
    # 供多份报告共享（auto_generate 提交时批量生成）；缺省则内部预取。
    if events_by_sk is None:
        events_by_sk = get_events_batch(
            session, class_student_ids, list(graph.grade7_kp_ids()), as_of
        )
    per_student: dict[int, list[KpAssessment]] = {
        sid: assess_student_kps(
            session, graph, sid, class_id, as_of, events_by_sk=events_by_sk
        )
        for sid in committed
    }
    kp_stats: dict[int, dict] = {}
    for assessments in per_student.values():
        for a in assessments:
            if a.gate is not None or a.mastery is None:
                continue
            st = kp_stats.setdefault(
                a.kp_id,
                {"code": a.kp_code, "name": a.kp_name, "values": [], "weak": 0, "n": 0},
            )
            st["values"].append(a.mastery)
            st["n"] += 1
            if a.is_weak:
                st["weak"] += 1

    common_weak: list[dict] = []
    for kp_id, st in kp_stats.items():
        if st["n"] >= 4:
            share = st["weak"] / st["n"]
            avg = sum(st["values"]) / len(st["values"])
            if share >= CLASS_COMMON_WEAK_RATIO:
                common_weak.append(
                    {
                        "code": st["code"],
                        "name": st["name"],
                        "class_avg": avg,
                        "weak_share": share,
                        "n": st["n"],
                    }
                )
    common_weak.sort(key=lambda d: d["class_avg"])

    # ---- 渲染 Markdown（数字全部由上方计算注入） ----
    lines: list[str] = []
    lines.append(f"# 考后质量分析：{template.name}")
    lines.append("")
    lines.append(f"- 班级：{clazz.name}　考试日期：{template.exam_date}　类型：{template.type}")
    lines.append(f"- 提交人数：{len(committed)} / {len(students)}（待审核 {pending} 人）")
    lines.append(
        f"- 说明：本报告由系统基于已提交成绩自动生成，数字均可追溯至逐题得分；"
        "不含任何排名信息。"
    )
    lines.append("")

    lines.append("## 一、总体情况")
    if totals:
        lines.append(
            f"卷面满分 {full_total:g} 分；班级平均 {statistics.mean(totals):.1f} 分，"
            f"最高 {max(totals):g} 分，最低 {min(totals):g} 分，"
            f"中位数 {statistics.median(totals):.1f} 分"
            + (f"，标准差 {statistics.pstdev(totals):.1f}" if len(totals) > 1 else "")
            + "。"
        )
    else:
        lines.append("尚无已提交的作答数据。")
    lines.append("")

    lines.append("## 二、逐题得分率")
    lines.append("")
    lines.append("| 题号 | 题型 | 满分 | 得分率 | 涉及知识点 | 状态 |")
    lines.append("|---|---|---|---|---|---|")
    for q in q_rates:
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
    for st in sorted(kp_stats.values(), key=lambda d: d["code"]):
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
    if common_weak:
        lines.append(
            "以下知识点待加强学生占比 ≥ "
            f"{CLASS_COMMON_WEAK_RATIO*100:.0f}%，属班里普遍问题，"
            "建议优先通过集体教学解决（不单独归到学生身上）："
        )
        lines.append("")
        for d in common_weak:
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
    if pending > 0:
        lines.append(f"- 仍有 {pending} 名学生的作答处于待审核状态，审核后重新生成本报告。")
    untagged = [q["idx"] for q in q_rates if not q["kps"]]
    if untagged:
        lines.append(f"- 题目 {untagged} 未标注知识点，不计入知识点分析。")
    lines.append("- 掌握程度按时间远近加权计算，越近的考试越重要；依据少于 3 题的知识点已按「数据不足」处理。")
    lines.append("")
    lines.append("---")
    lines.append("*本报告为初步教学参考；原因分析均需教师确认后使用。*")

    markdown = "\n".join(lines)
    if narrative:
        from app.reports.narrative import render_narrative

        section = render_narrative(markdown, "quality_analysis")
        if section:
            markdown += section
    snapshot = {
        "class": clazz.name,
        "exam": template.name,
        "exam_date": str(template.exam_date),
        "committed": len(committed),
        "pending": pending,
        "stats": {
            "mean": round(statistics.mean(totals), 2) if totals else None,
            "max": max(totals) if totals else None,
            "min": min(totals) if totals else None,
        },
        "question_rates": q_rates,
        "common_weak": common_weak,
    }

    report = Report(
        type="quality_analysis",
        class_id=class_id,
        exam_id=exam_id,  # 关联到具体考试（提交后自动生成 / get-or-generate 落库）
        snapshot_json=snapshot,
        content_markdown=markdown,
    )
    session.add(report)
    session.flush()
    return report
