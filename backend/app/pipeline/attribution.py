"""归因层规则引擎（DESIGN §7）——归因 = 带证据、可证伪的方向性假设。

MVP 实现三类：
- 前置缺陷：沿 prerequisite 边下探 ≤3 层，前置点掌握度同步低 → 定位根源知识点；
- 遗忘衰减：历史掌握度曾高、长期无证据、当前显著低于峰值；
- 数据不足：证据低于门槛 → 明示不判断（本身就是结论，防过度归因）。

三条纪律的代码落点：
1. 可证伪：每条归因附带 prediction（诊断小测可验证）；
2. 班级参照：班级共性薄弱 → is_class_common=True，转教学建议，不向学生归责；
3. 稳定性：仅对通过证据门槛（≥3 题）的知识点归因；重复运行做 upsert，
   教师 overridden 的归因永不被引擎复活（教师否决权）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import (
    EVIDENCE_LOW_WATERMARK,
    FORGET_DROP,
    FORGET_IDLE_DAYS,
    FORGET_PEAK_THRESHOLD,
    GLOBAL_WEAK_CONF_CAP,
    GLOBAL_WEAK_MIN_SAMPLE,
    GLOBAL_WEAK_RATIO,
    MIN_EVIDENCE_COUNT,
    PREREQ_MAX_DEPTH,
    PREREQ_ROOT_THRESHOLD,
)
from app.kb.graph import KpGraph
from app.models import Attribution, Student, TeachingProgress
from app.pipeline.mastery import evidence_summary, mastery_at, mastery_series
from app.pipeline.weakness import (
    GATE_INSUFFICIENT,
    GATE_NOT_LEARNED,
    KpAssessment,
    assess_student_kps,
    covered_kp_ids,
)

ATTR_PREREQ = "前置缺陷"
ATTR_FORGET = "遗忘衰减"
ATTR_INSUFFICIENT = "数据不足"
ATTR_CONFUSABLE = "易混淆"


@dataclass
class AttributionFinding:
    """一条归因假设（尚未落库的内存结构）。"""

    kp_id: int
    type: str
    confidence: float
    root_kp_id: int | None = None
    evidence: list[dict] = field(default_factory=list)
    prediction: str = ""


@dataclass
class ResolvedAttribution:
    """该生某归因的读视图（候选1：推导假设 ⊕ 人工裁决叠加，derive-on-read）。

    ``verdict``：active（系统推导，未被裁决）| overridden（教师否决/诊断题证伪）。
    只读渲染用；不写库。与持久化 ``Attribution``（裁决记录/审计）解耦。
    """

    kp_id: int
    type: str
    confidence: float
    root_kp_id: int | None = None
    evidence: list[dict] = field(default_factory=list)
    prediction: str = ""
    verdict: str = "active"
    teacher_note: str | None = None


def attribute_assessment(
    session: Session,
    graph: KpGraph,
    student_id: int,
    a: KpAssessment,
    covered: set[int],
    as_of: datetime,
) -> list[AttributionFinding]:
    """对单个知识点评估结果产出归因假设（多假设并存）。"""
    if a.gate == GATE_NOT_LEARNED:
        return []  # 未学到不归因，报告直接说明
    if a.gate == GATE_INSUFFICIENT:
        return [
            AttributionFinding(
                kp_id=a.kp_id,
                type=ATTR_INSUFFICIENT,
                confidence=1.0,
                evidence=[
                    {"evidence_count": a.evidence_count, "required": 3},
                ],
                prediction=(
                    f"当前关于「{a.kp_name}」的依据不足（{a.evidence_count} 题），"
                    "暂不判断原因；补充针对性练习或下次考试后再评估。"
                ),
            )
        ]
    if a.low_evidence:
        return []  # 依据较少（< EVIDENCE_LOW_WATERMARK）：可评估但不下因果归因，避免稀疏数据上伪因果
    if not a.is_weak:
        return []

    findings: list[AttributionFinding] = []
    findings.extend(_prereq_deficit(session, graph, student_id, a, covered, as_of))
    findings.extend(_forgetting_decay(session, graph, student_id, a, as_of))
    findings.extend(_confusable_pair(session, graph, student_id, a, as_of))
    return findings


def _prereq_deficit(
    session: Session,
    graph: KpGraph,
    student_id: int,
    a: KpAssessment,
    covered: set[int],
    as_of: datetime,
) -> list[AttributionFinding]:
    """前置缺陷：≤3 层内的前置点掌握度同步低。"""
    low_ancestors: list[tuple[int, int, float, float | None]] = []  # (kp_id, depth, edge_w, mastery)
    for anc_id, depth, edge_w in graph.prerequisite_chain(a.kp_id, PREREQ_MAX_DEPTH):
        summary = evidence_summary(session, student_id, anc_id, as_of)
        if summary.count < EVIDENCE_LOW_WATERMARK:
            continue  # 前置根源要求充分证据（评估从宽、归因从严：MIN 可降但根源须 3 证据）
        m = mastery_at(session, student_id, anc_id, as_of)
        if m is not None and m < PREREQ_ROOT_THRESHOLD:
            low_ancestors.append((anc_id, depth, edge_w, m))

    if not low_ancestors:
        return []

    # 根源 = 掌握度缺口 × 前置强度（缺口主导 0.5+0.5·w，weight 作微调，improvement-plan §2.3）；
    # 并列时取更浅的直接前置（更可能是成因，越深越像远端噪声）。
    def _root_score(t: tuple[int, int, float, float | None]) -> float:
        _, _, edge_w, m = t
        gap = PREREQ_ROOT_THRESHOLD - (m if m is not None else 1.0)
        return gap * (0.5 + 0.5 * edge_w)

    low_ancestors.sort(key=lambda t: (-_root_score(t), t[1]))
    root_id, root_depth, _root_w, root_m = low_ancestors[0]
    root = graph.kp(root_id)

    confidence = min(0.9, 0.6 + 0.1 * len(low_ancestors))
    if root_m is not None and root_m < 0.4:
        confidence = min(0.95, confidence + 0.05)

    evidence = [
        {
            "ancestor": graph.kp(anc_id).code,
            "ancestor_name": graph.kp(anc_id).name,
            "depth": depth,
            "edge_weight": round(edge_w, 2),
            "mastery": round(m, 3) if m is not None else None,
        }
        for anc_id, depth, edge_w, m in low_ancestors
    ]

    prediction = (
        f"如果是基础没打牢，让该生单独做几道「{root.name}」的诊断题"
        f"（不涉及「{a.kp_name}」本身），正确率也应低于 60%。"
        "可用 2~3 道诊断题验证；若诊断通过，说明这个原因不成立。"
    )

    return [
        AttributionFinding(
            kp_id=a.kp_id,
            type=ATTR_PREREQ,
            confidence=round(confidence, 2),
            root_kp_id=root_id,
            evidence=evidence,
            prediction=prediction,
        )
    ]


def _forgetting_decay(
    session: Session,
    graph: KpGraph,
    student_id: int,
    a: KpAssessment,
    as_of: datetime,
) -> list[AttributionFinding]:
    """遗忘衰减：掌握度曾高（≥0.75）→ 峰值后长期无证据（间隔 ≥30 天）→
    新证据显示显著回落。遗忘在本模型中只能通过"间隔后的新证据"观察到。"""
    series = mastery_series(session, student_id, a.kp_id, as_of)
    if len(series) < 2:
        return []
    peak_t, peak = max(series, key=lambda p: p[1])
    last_t = series[-1][0]
    current = a.mastery if a.mastery is not None else series[-1][1]
    if peak < FORGET_PEAK_THRESHOLD:
        return []
    if current > peak - FORGET_DROP:
        return []
    if last_t <= peak_t:
        return []  # 峰值就是最新证据 → 尚未观察到回落
    if (last_t - peak_t).days < FORGET_IDLE_DAYS:
        return []  # 峰值与新证据间隔太短 → 更像持续薄弱而非遗忘

    return [
        AttributionFinding(
            kp_id=a.kp_id,
            type=ATTR_FORGET,
            confidence=0.7,
            evidence=[
                {"peak_mastery": round(peak, 3), "peak_at": str(peak_t.date())},
                {"current_mastery": round(current, 3)},
                {"gap_days": (last_t - peak_t).days},
            ],
            prediction=(
                f"如果是学过但忘了，安排 2~3 次间隔复习后，「{a.kp_name}」的掌握程度"
                "应回升至 0.75 附近；若复习后仍低迷，应考虑其他原因。"
            ),
        )
    ]


def _confusable_pair(
    session: Session,
    graph: KpGraph,
    student_id: int,
    a: KpAssessment,
    as_of: datetime,
) -> list[AttributionFinding]:
    """易混淆归因（kb-improvement-design K1）：薄弱 KP 的易混伙伴也弱 → 概念混淆假设。

    数学高频错因之一是把两个相近概念搞混（相反数↔倒数、单项式↔多项式等），
    这类学生不是「前置没打牢」也不是「忘了」，而是区分度不足。判断依据：
    薄弱 KP 的 confusable 伙伴同样薄弱（证据充分、mastery 低于根因阈值）。
    """
    partners = graph.confusable_partners(a.kp_id)
    if not partners:
        return []
    weak_partners: list[tuple[int, float]] = []
    for pid in partners:
        summary = evidence_summary(session, student_id, pid, as_of)
        if summary.count < EVIDENCE_LOW_WATERMARK:
            continue  # 伙伴依据不足：不强行下假设（评估从宽、归因从严）
        m = mastery_at(session, student_id, pid, as_of)
        if m is not None and m < PREREQ_ROOT_THRESHOLD:
            weak_partners.append((pid, m))

    if not weak_partners:
        return []

    first_pid, _ = weak_partners[0]
    first = graph.kp(first_pid)
    evidence = [
        {
            "confused_with": graph.kp(pid).code,
            "confused_with_name": graph.kp(pid).name,
            "mastery": round(m, 3),
        }
        for pid, m in weak_partners
    ]

    return [
        AttributionFinding(
            kp_id=a.kp_id,
            type=ATTR_CONFUSABLE,
            confidence=0.65,
            evidence=evidence,
            prediction=(
                f"如果是混淆了「{a.kp_name}」与「{first.name}」，"
                f"做区分两者的 2~3 道对比诊断题该生也会错；"
                "若对比题能正确区分，说明并非概念混淆。"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# 落库：重跑时 upsert，教师否决（overridden）永不被引擎复活
# ---------------------------------------------------------------------------


def run_attribution_for_student(
    session: Session,
    graph: KpGraph,
    student_id: int,
    class_id: int,
    as_of: datetime,
) -> list[Attribution]:
    """全量重算该生归因并落库，返回当前 active 归因列表。"""
    assessments = assess_student_kps(session, graph, student_id, class_id, as_of)
    covered = covered_kp_ids(session, class_id, as_of)

    findings: list[AttributionFinding] = []
    for a in assessments:
        for f in attribute_assessment(session, graph, student_id, a, covered, as_of):
            findings.append(f)

    # 全局薄弱抑制（V3）：评估该生在已覆盖知识点中的薄弱广度。若多数知识点薄弱，
    # 前置缺陷的「特定根源」解释力下降（更可能整体基础/学习状态问题）->
    # 下调前置缺陷归因置信度并标注。不删除归因（仍是待验证假设），targeted-weak
    # 学生（弱 kp 占比低）不触发，金标基线不受影响。
    # 重要度加权（kb-improvement-design K5）：基础级薄弱 ×1.5、拓展级 ×0.5 计入，
    # 避免「拓展题做不好」被当成「全局基础差」。缺省（未标注）按核心权重 1.0。
    _IMP_WEIGHT = {"基础": 1.5, "核心": 1.0, "拓展": 0.5}

    def _imp_w(a: KpAssessment) -> float:
        return _IMP_WEIGHT.get(getattr(graph.kp(a.kp_id), "importance", "核心"), 1.0)

    valid_assessed = [a for a in assessments if a.gate is None and a.mastery is not None]
    if len(valid_assessed) >= GLOBAL_WEAK_MIN_SAMPLE:
        total_w = sum(_imp_w(a) for a in valid_assessed)
        weak_w = sum(_imp_w(a) for a in valid_assessed if a.is_weak)
        if total_w > 0 and (weak_w / total_w) >= GLOBAL_WEAK_RATIO:
            weak_frac = round(weak_w / total_w, 2)
            for f in findings:
                if f.type == ATTR_PREREQ:
                    f.confidence = round(min(f.confidence, GLOBAL_WEAK_CONF_CAP), 2)
                    f.evidence.append({
                        "global_weak": True,
                        "weak_fraction": weak_frac,
                        "note": "学生在多数知识点薄弱，前置缺陷归因解释力下降，建议优先排查整体基础与学习状态",
                    })

    existing = {
        (att.kp_id, att.type): att
        for att in session.scalars(
            select(Attribution).where(
                Attribution.student_id == student_id,
                Attribution.status.in_(["active", "overridden"]),
            )
        )
    }

    result: list[Attribution] = []
    seen_keys: set[tuple[int, str]] = set()
    for f in findings:
        key = (f.kp_id, f.type)
        seen_keys.add(key)
        att = existing.get(key)
        if att is not None:
            if att.status == "overridden":
                result.append(att)
                continue  # 教师已否决：保留否决记录，不更新为 active
            att.confidence = f.confidence
            att.root_kp_id = f.root_kp_id
            att.evidence_json = f.evidence
            att.prediction = f.prediction
        else:
            att = Attribution(
                student_id=student_id,
                kp_id=f.kp_id,
                type=f.type,
                confidence=f.confidence,
                root_kp_id=f.root_kp_id,
                evidence_json=f.evidence,
                prediction=f.prediction,
                status="active",
            )
            session.add(att)
        result.append(att)

    # 不再成立的旧 active 归因 → resolved（保留历史，不物理删除）
    for key, att in existing.items():
        if key not in seen_keys and att.status == "active":
            att.status = "resolved"

    session.flush()
    return [a for a in result if a.status == "active"]


# ---------------------------------------------------------------------------
# 诊断题证伪闭环（improvement-plan §1.4-A）
# 前置缺陷归因预测「该生做前置点的独立诊断题也应失败」。诊断题（type=诊断，
# 单 kp）作答提交后派生单 kp 证据；本函数用该证据验证预测，证伪则置 overridden
# （跨重跑保留），证实则记录确认（保留 active）。让归因从「纸面预测」变可证伪。
# ---------------------------------------------------------------------------


def verify_attribution_prediction(
    session: Session,
    graph: KpGraph,
    attribution_id: int,
    as_of: datetime,
) -> dict:
    """用诊断证据验证一条前置缺陷归因的预测，返回判定结果并更新状态。"""
    att = session.get(Attribution, attribution_id)
    if att is None:
        raise ValueError("归因不存在")
    if att.status != "active":
        raise ValueError(f"归因状态为 {att.status}，无需验证")
    if att.type != ATTR_PREREQ or att.root_kp_id is None:
        raise ValueError(f"归因类型「{att.type}」不支持诊断题证伪（仅前置缺陷）")

    root = graph.kp(att.root_kp_id)
    summary = evidence_summary(session, att.student_id, att.root_kp_id, as_of)
    m = mastery_at(session, att.student_id, att.root_kp_id, as_of)
    date_str = as_of.strftime("%Y-%m-%d")

    if summary.count < MIN_EVIDENCE_COUNT:
        verdict = "inconclusive"
        note = (
            f"诊断证据不足（{summary.count} 题 < {MIN_EVIDENCE_COUNT}），"
            f"无法证伪（{date_str}）"
        )
    elif m is not None and m >= PREREQ_ROOT_THRESHOLD:
        verdict = "falsified"
        note = (
            f"诊断题证伪（{date_str}）：前置点「{root.name}」掌握度 {m:.2f}"
            f" 已达标（≥{PREREQ_ROOT_THRESHOLD}），前置缺陷假设不成立"
        )
        att.status = "overridden"
    else:
        verdict = "supported"
        mv = m if m is not None else 0.0
        note = (
            f"诊断题证实（{date_str}）：前置点「{root.name}」掌握度 {mv:.2f}"
            f" 仍低于阈值，假设成立"
        )

    att.teacher_note = note
    session.flush()
    return {
        "attribution_id": att.id,
        "verdict": verdict,
        "root_kp_id": att.root_kp_id,
        "root_kp_code": root.code,
        "root_kp_name": root.name,
        "root_mastery": round(m, 3) if m is not None else None,
        "evidence_count": summary.count,
        "status": att.status,
        "note": note,
    }


# ---------------------------------------------------------------------------
# 证伪闭环度量（effectiveness-validation-plan V3-度量）
# 让「可证伪」从纸面承诺变可观测：多少归因被诊断题验证过、证伪/证实/未决比例、
# 教师否决率。闭合率低 = 「可证伪」停留在纸面。verify_attribution_prediction
# 写入 teacher_note 时以「诊断题证伪/证实/无法证伪」标识结论，此处据此分类。
# ---------------------------------------------------------------------------


def _classify_verdict(note: str | None) -> str | None:
    """从 teacher_note 解析诊断验证结论；非诊断验证（人工否决）返回 None。

    verify_attribution_prediction 写入的 note：
    - falsified:   「诊断题证伪（...）：...假设不成立」
    - supported:   「诊断题证实（...）：...假设成立」
    - inconclusive:「诊断证据不足（...），无法证伪（...）」  ← 注意不含「诊断题」
    故按 verdict 标记词分类，且必须先判「无法证伪」（它也含「证伪」二字）。
    """
    if not note:
        return None
    if "无法证伪" in note:
        return "inconclusive"
    if "诊断题证伪" in note:
        return "falsified"
    if "诊断题证实" in note:
        return "supported"
    return None


def attribution_closure(session: Session, class_id: int | None = None) -> dict:
    """归因按状态/证伪结论的分布（只读，不改不变量②）。"""
    stmt = select(Attribution)
    if class_id is not None:
        stmt = stmt.join(Student, Student.id == Attribution.student_id).where(
            Student.class_id == class_id
        )
    atts = list(session.scalars(stmt))

    by_status: dict[str, int] = {"active": 0, "overridden": 0, "resolved": 0}
    by_verdict: dict[str, int] = {"falsified": 0, "supported": 0, "inconclusive": 0}
    diag_verified = 0
    for att in atts:
        by_status[att.status] = by_status.get(att.status, 0) + 1
        v = _classify_verdict(att.teacher_note)
        if v is not None:
            by_verdict[v] = by_verdict.get(v, 0) + 1
            diag_verified += 1

    total = len(atts)
    # 人工否决 = overridden 总数 - 诊断证伪数（诊断证伪也置 overridden）
    teacher_overridden = max(0, by_status.get("overridden", 0) - by_verdict.get("falsified", 0))
    return {
        "total": total,
        "by_status": by_status,
        "by_verdict": by_verdict,
        "diagnostic_verified": diag_verified,
        "teacher_overridden": teacher_overridden,
        "closure_rate": round(diag_verified / total, 3) if total else 0.0,
    }
