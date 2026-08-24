"""证据包（diagnosis-sheet-redesign.md §2.2）：把已有计算层输出序列化为紧凑 JSON。

分层原则：规则引擎产证据（掌握度/归因候选/共性薄弱，全部确定性计算），
LLM 拿到证据包做研判与落笔——数字与名称全部系统注入，模型不得引入材料外的
数字/知识点/结论（不变量④在生成层的延伸）。

纪律：
- 学生包只含本人数据（无他人姓名/成绩/排名）；
- 班级包只有聚合数（不含任何学生个体数据、不含名单）。

本模块纯函数：输入计算层产物 → 输出 dict；无 DB、无 LLM。
"""

from __future__ import annotations

from datetime import datetime

from app.kb.graph import KpGraph
from app.pipeline.weakness import TRAJ_RISING, KpAssessment
from app.reports.diagnosis_model import DiagnosisReportModel
from app.reports.labels import attr_label, traj_label
from app.reports.quality_model import QualityReportModel


def _pct(x: float | None) -> int | None:
    """0.83 → 83（证据包统一用整数百分比，省 token 且避免小数噪声）。"""
    return round(x * 100) if x is not None else None


def student_evidence_pack(
    graph: KpGraph, model: DiagnosisReportModel, kind: str = "student_diagnosis"
) -> dict:
    """学生证据包：诊断单/改进单共用一份输入（结构相同，kind 标注用途）。

    来源即 ``compute_diagnosis_model`` 的产物——薄弱/进步/归因候选全部来自
    确定性引擎；LLM 只解释与综合表述，不改写数值。
    """
    weak = [
        {
            "kp": a.kp_name,
            "code": a.kp_code,
            "mastery_pct": _pct(a.mastery),
            "criterion": a.weak_criterion,
            "evidence_count": a.evidence_count,
            "trajectory": traj_label(a.trajectory),
            "class_common": a.is_class_common,
            "stale": a.stale,
        }
        for a in model.weak
    ]
    progress = [
        {
            "kp": a.kp_name,
            "mastery_pct": _pct(a.mastery),
            "evidence_count": a.evidence_count,
            "rising": a.trajectory == TRAJ_RISING,
        }
        for a in model.progress
    ]
    attributions = []
    for a in model.weak:
        att = model.attributions.get(a.kp_id)
        if att is None:
            attributions.append({"kp": a.kp_name, "type": None})
            continue
        root = graph.kp(att.root_kp_id).name if att.root_kp_id else None
        attributions.append(
            {
                "kp": a.kp_name,
                "type": attr_label(att.type),
                "confidence_pct": _pct(att.confidence),
                "root_kp": root,
                # 验证方式（诊断题证伪闭环的预测），LLM 应转述为「怎么验证」
                "verification": att.prediction or "",
            }
        )
    return {
        "kind": kind,
        "alias": model.student_alias,
        "class": model.class_name,
        "as_of": str(model.as_of.date()),
        "progress": progress,
        "weak": weak,
        "attributions": attributions,
        "not_learned_count": len(model.not_learned),
        "insufficient_count": len(model.insufficient),
    }


def class_evidence_pack(
    graph: KpGraph,
    quality: QualityReportModel,
    as_of: datetime | None = None,
    trend_summary: dict | None = None,
) -> dict:
    """班级证据包（班级改进意见的输入）：纯聚合数，零个体数据。

    ``trend_summary`` 为近两场趋势摘要（可选）：{"prev_exam", "entered", "exited"}，
    进入/退出共性榜的点数变化。干预行统计（intervention-loop 落地后）预留扩展位。
    """
    common_weak = [
        {
            "kp": d["name"],
            "class_avg_pct": _pct(d["class_avg"]),
            "weak_share_pct": _pct(d["weak_share"]),
            "n": d["n"],
        }
        for d in quality.common_weak[:5]
    ]
    low_rate_questions = [
        {"idx": q["idx"], "rate_pct": _pct(q["rate"]), "kps": q["kps"]}
        for q in quality.question_rates
        if q.get("low")
    ][:5]
    pack = {
        "kind": "class_improvement_advice",
        "exam": quality.exam_name,
        "as_of": str(as_of.date()) if as_of else quality.exam_date,
        "committed": quality.committed,
        "pending": quality.pending,
        "mean_score_pct": _pct(
            (sum(quality.totals) / len(quality.totals)) / quality.full_total
            if quality.totals and quality.full_total > 0
            else None
        ),
        "common_weak": common_weak,
        "low_rate_questions": low_rate_questions,
    }
    if trend_summary:
        pack["trend"] = trend_summary
    return pack
