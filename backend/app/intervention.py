"""干预闭环纯计算层（intervention-loop-design.md §1-§4）。

三块职责，全部确定性计算（一期零 LLM，不变量④）：

1. 策略映射 ``generate_interventions``：归因四类 + 认知层级断层 + 数据不足 +
   班级共性 → 封闭集合的 kind；按 root_kp_id 聚类成组；杠杆降序排序。
2. 幂等再生成（§3）：清除本场旧 suggested、保留 done/skipped（执行事实是历史，
   与归因 override 同一纪律）；已干预待复测不重发（防建议轰炸）；干预后仍有
   新证据且仍薄弱 → 二次干预升级。
3. 效果推导 ``intervention_effect``（§4）：derive-on-read，不存任何效果快照。
   基线调整（扣除同 kp 班级同期变化）对冲向均值回归——产出是带证据的方向性
   判断，不是因果结论。

边界声明（设计 §0）：建议只覆盖知识维度的学法与教学安排，不涉及动机、情绪等
不可见因素——全部由「归因类型 + 图谱结构」确定性推出，是对证据的回应。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import CLASS_COMMON_WEAK_RATIO
from app.kb.graph import KpGraph
from app.models import (
    Attribution,
    EvidenceEvent,
    ExamResponse,
    Intervention,
    Student,
)
from app.pipeline.attribution import (
    ATTR_CONFUSABLE,
    ATTR_FORGET,
    ATTR_INSUFFICIENT,
    ATTR_PREREQ,
    resolve_attributions,
)
from app.pipeline.mastery import get_events, mastery_at
from app.pipeline.weakness import (
    GATE_INSUFFICIENT,
    KpAssessment,
    assess_student_kps,
    covered_kp_ids,
)

# ---------------------------------------------------------------------------
# 配置（设计 §8，全部带默认值）
# ---------------------------------------------------------------------------

ACTION_PLAN_ENABLE = os.environ.get("SC_ACTION_PLAN_ENABLE", "1").lower() in ("1", "true", "yes")
INTERVENTION_MIN_DELTA = float(os.environ.get("SC_INTERVENTION_MIN_DELTA", "0.10"))
INTERVENTION_FLAT_FLOOR = float(os.environ.get("SC_INTERVENTION_FLAT_FLOOR", "-0.05"))
ACTION_GROUP_MIN = int(os.environ.get("SC_ACTION_GROUP_MIN", "3"))

# 认知层级断层触发线：识记 ≥ floor 且应用 < floor - 0.10（设计 §1 表格）
TIER_DRILL_COG_GAP = 0.10

KIND_RETEACH = "reteach"                      # 重讲+变式（班级）
KIND_PREREQ_BACKFILL = "prereq_backfill"      # 回补根源
KIND_SPACED_REVIEW = "spaced_review"          # 间隔复习
KIND_CONTRAST_PRACTICE = "contrast_practice"  # 概念辨析
KIND_EVIDENCE_BOOST = "evidence_boost"        # 补证据练习
KIND_TIER_DRILL = "tier_drill"                # 层级补强

SCOPE_CLASS = "class"
SCOPE_GROUP = "group"
SCOPE_STUDENT = "student"


@dataclass
class ActionRow:
    """一条行动建议（内存结构）：聚类改写 scope 后统一落库。"""

    student_id: int | None   # None = 全班
    kp_id: int
    kind: str
    scope: str
    attribution_id: int | None = None
    root_kp_id: int | None = None   # prereq 的根源 / contrast 的易混伙伴
    note: str | None = None


def _tier_drill_hit(a: KpAssessment) -> bool:
    """认知层级断层（kb-improvement-design K7-B 消费方）：识记达标、应用明显落后。

    仅在薄弱点上触发（与归因触发同纪律：attribute_assessment 也只在薄弱时产出），
    避免整体达标的点因分维抖动产生噪声建议。
    """
    if not a.is_weak or not a.per_cog_mastery:
        return False
    recall = a.per_cog_mastery.get("识记")
    apply_ = a.per_cog_mastery.get("应用")
    if recall is None or apply_ is None:
        return False
    return recall >= a.floor and apply_ < a.floor - TIER_DRILL_COG_GAP


def _pick_attribution(
    candidates: list,
) -> tuple[str | None, int | None, int | None]:
    """一个薄弱点的多条归因假设中选最对症的一条：(type, root_kp_id, partner_kp_id)。

    优先级 前置缺陷 > 遗忘衰减 > 易混淆——前置是结构性根源，先补地基；
    与诊断单展示互不影响（诊断逐条列出全部候选）。
    """
    by_type = {c.type: c for c in candidates}
    if ATTR_PREREQ in by_type:
        c = by_type[ATTR_PREREQ]
        return ATTR_PREREQ, c.root_kp_id, None
    if ATTR_FORGET in by_type:
        return ATTR_FORGET, None, None
    if ATTR_CONFUSABLE in by_type:
        c = by_type[ATTR_CONFUSABLE]
        # 易混伙伴取证据里第一个（与归因推导的排序一致）
        partner = None
        for ev in c.evidence:
            code = ev.get("confused_with") if isinstance(ev, dict) else None
            if code:
                partner = code
                break
        return ATTR_CONFUSABLE, None, partner
    return None, None, None


def _student_action_rows(
    session: Session,
    graph: KpGraph,
    student_id: int,
    class_id: int,
    as_of: datetime,
    *,
    assessments: list[KpAssessment],
) -> list[ActionRow]:
    """单个学生的个体行动行（六种触发条件的映射核心）。

    ``assessments`` 由调用方传入（与班级共性统计共享一次评估）；归因走
    derive-on-read（overridden 不产生建议——教师已否决的假设不再推荐行动）。
    """
    attributions: dict[int, list] = {}
    for r in resolve_attributions(
        session, graph, student_id, class_id, as_of, assessments=assessments
    ):
        if r.verdict == "active":
            attributions.setdefault(r.kp_id, []).append(r)
    # 物化后的归因行 id（auto_generate 中物化先于本函数执行；查不到则留空）
    att_ids = {
        (att.kp_id, att.type): att.id
        for att in session.scalars(
            select(Attribution).where(
                Attribution.student_id == student_id,
                Attribution.status == "active",
            )
        )
    }

    rows: list[ActionRow] = []
    for a in assessments:
        # 触发条件优先级（一个薄弱点只产一条最对症的建议，避免轰炸）：
        # 数据不足 → 补证据；未学到 → 不建行；其余按 归因优先级 / 层级断层。
        if a.gate == GATE_INSUFFICIENT:
            rows.append(
                ActionRow(student_id=student_id, kp_id=a.kp_id,
                          kind=KIND_EVIDENCE_BOOST, scope=SCOPE_STUDENT)
            )
            continue
        if a.gate is not None or a.mastery is None or not a.is_weak:
            continue

        att_type, root_kp_id, partner_code = _pick_attribution(
            attributions.get(a.kp_id, [])
        )

        if _tier_drill_hit(a):
            rows.append(
                ActionRow(student_id=student_id, kp_id=a.kp_id,
                          kind=KIND_TIER_DRILL, scope=SCOPE_STUDENT)
            )
            continue

        if att_type is None:
            # 未匹配成因：不建行（报告保留「建议教师结合课堂观察研判」，避免噪声）
            continue

        if att_type == ATTR_PREREQ and root_kp_id is not None:
            rows.append(
                ActionRow(
                    student_id=student_id, kp_id=a.kp_id,
                    kind=KIND_PREREQ_BACKFILL, scope=SCOPE_STUDENT,
                    attribution_id=att_ids.get((a.kp_id, ATTR_PREREQ)),
                    root_kp_id=root_kp_id,
                )
            )
        elif att_type == ATTR_FORGET:
            rows.append(
                ActionRow(student_id=student_id, kp_id=a.kp_id,
                          kind=KIND_SPACED_REVIEW, scope=SCOPE_STUDENT,
                          attribution_id=att_ids.get((a.kp_id, ATTR_FORGET)))
            )
        elif att_type == ATTR_CONFUSABLE:
            partner_id = None
            if partner_code:
                try:
                    partner_id = graph.code(partner_code)
                except KeyError:
                    partner_id = None
            if partner_id is None:
                partners = graph.confusable_partners(a.kp_id)
                partner_id = partners[0] if partners else None
            rows.append(
                ActionRow(
                    student_id=student_id, kp_id=a.kp_id,
                    kind=KIND_CONTRAST_PRACTICE, scope=SCOPE_STUDENT,
                    attribution_id=att_ids.get((a.kp_id, ATTR_CONFUSABLE)),
                    root_kp_id=partner_id,
                )
            )
    return rows


def generate_interventions(
    session: Session,
    graph: KpGraph,
    class_id: int,
    exam_id: int,
    as_of: datetime,
    source_report_id: int | None = None,
    *,
    events_by_sk: dict[tuple[int, int], list[EvidenceEvent]] | None = None,
) -> dict:
    """提交考试后的建议生成 + 落库（auto_generate 尾步调用，同 savepoint best-effort）。

    幂等纪律（§3）：
    - 清除本场旧的 suggested 行（未确认的建议随新评估刷新）；
    - done/skipped 全班范围保留（执行事实是历史，跨重跑保留）;
    - 同生同点同 kind 已 done 且 done_at 后无新证据 → 不重复建议（防轰炸）；
    - done_at 后有新证据且本次评估仍薄弱 → 二次干预（note 预填升级说明）。

    返回 {"suggested": 行数, "groups": 成组数}。
    """
    if not ACTION_PLAN_ENABLE:
        return {"suggested": 0, "groups": 0}

    committed_ids = sorted(
        set(
            session.scalars(
                select(ExamResponse.student_id).where(
                    ExamResponse.exam_template_id == exam_id,
                    ExamResponse.status == "已提交",
                )
            )
        )
    )
    if not committed_ids:
        return {"suggested": 0, "groups": 0}

    # ---- 清除本场旧 suggested（done/skipped 不动）----
    for row in session.scalars(
        select(Intervention).where(
            Intervention.exam_id == exam_id,
            Intervention.status == "suggested",
        )
    ):
        session.delete(row)
    session.flush()

    kept_done: dict[tuple[int, int, str], Intervention] = {}
    for att in session.scalars(
        select(Intervention).where(
            Intervention.class_id == class_id,
            Intervention.status == "done",
            Intervention.student_id.is_not(None),
        )
    ):
        kept_done[(att.student_id, att.kp_id, att.kind)] = att

    # ---- 每生一次评估，个体行与班级共性共享 ----
    individual: dict[int, list[ActionRow]] = {}
    weak_count: dict[int, int] = {}
    n_assessed: dict[int, int] = {}
    for sid in committed_ids:
        assessments = assess_student_kps(
            session, graph, sid, class_id, as_of, events_by_sk=events_by_sk
        )
        rows = _student_action_rows(session, graph, sid, class_id, as_of, assessments=assessments)
        kept_rows: list[ActionRow] = []
        for r in rows:
            prev = kept_done.get((sid, r.kp_id, r.kind))
            if prev is not None and prev.done_at is not None:
                post = [
                    e
                    for e in get_events(session, sid, r.kp_id, as_of)
                    if e.occurred_at > prev.done_at
                ]
                if not post:
                    continue  # 已干预待复测：不重复建议（防轰炸）
                r.note = "二次干预：首次干预后复测仍待加强"
            kept_rows.append(r)
        individual[sid] = kept_rows
        # 共性统计复用同一份评估（gate/无掌握度的不计分母）
        for a in assessments:
            if a.gate is not None or a.mastery is None:
                continue
            n_assessed[a.kp_id] = n_assessed.get(a.kp_id, 0) + 1
            if a.is_weak:
                weak_count[a.kp_id] = weak_count.get(a.kp_id, 0) + 1

    # ---- 分组聚类（I2）：prereq_backfill 按 root_kp_id 聚类 ----
    group_members: dict[int, list[int]] = {}   # root_kp_id -> [student_id]（roster 原序）
    for sid in committed_ids:
        for r in individual[sid]:
            if r.kind == KIND_PREREQ_BACKFILL and r.root_kp_id is not None:
                group_members.setdefault(r.root_kp_id, []).append(sid)

    group_count = 0
    for root_kp_id, members in group_members.items():
        if len(members) < ACTION_GROUP_MIN:
            continue
        group_count += 1
        for sid in members:  # 名单原序：聚类不是排名，不暴露先后
            row = next(
                r
                for r in individual[sid]
                if r.kind == KIND_PREREQ_BACKFILL and r.root_kp_id == root_kp_id
            )
            row.scope = SCOPE_GROUP
            row.note = f"{len(members)} 人同根源薄弱，建议小组补学"

    # ---- 班级行：全班共性薄弱 → reteach ----
    class_rows: list[ActionRow] = []
    for kp_id, c in sorted(weak_count.items()):
        n = n_assessed.get(kp_id, 0)
        if n >= 4 and c / n >= CLASS_COMMON_WEAK_RATIO:
            class_rows.append(
                ActionRow(
                    student_id=None, kp_id=kp_id,
                    kind=KIND_RETEACH, scope=SCOPE_CLASS,
                    note=f"{c}/{n} 人待加强",
                )
            )

    # ---- 物化落库 ----
    created = 0
    for row in class_rows:
        session.add(
            Intervention(
                class_id=class_id, student_id=None, kp_id=row.kp_id,
                exam_id=exam_id, source_report_id=source_report_id,
                kind=row.kind, scope=row.scope, group_ref=None,
                baseline_as_of=as_of, status="suggested",
                note=f"{row.note}，建议下节课前 15 分钟重讲 + 变式训练",
            )
        )
        created += 1
    for sid in committed_ids:
        for row in individual[sid]:
            session.add(
                Intervention(
                    class_id=class_id, student_id=sid, kp_id=row.kp_id,
                    exam_id=exam_id, source_report_id=source_report_id,
                    kind=row.kind, scope=row.scope,
                    group_ref=(
                        f"r{source_report_id or 0}:{row.root_kp_id}"
                        if row.scope == SCOPE_GROUP
                        else None
                    ),
                    baseline_as_of=as_of, status="suggested", note=row.note,
                )
            )
            created += 1

    session.flush()
    return {"suggested": created, "groups": group_count}


# ---------------------------------------------------------------------------
# 效果验证（§4）：derive-on-read，零快照
# ---------------------------------------------------------------------------


def intervention_effect(
    session: Session,
    graph: KpGraph,
    intervention_id: int,
    now: datetime | None = None,
) -> dict:
    """单条干预的效果推导。pre/post 全部 mastery_at 现算，不存任何效果快照。

    - awaiting_retest：done 但无干预后证据（试点期常态，措辞正常化）；
    - improved / flat / declined：基线调整后的方向性判定；
    - suggested/skipped 行无效果语义（effect_status=not_executed）。
    """
    iv = session.get(Intervention, intervention_id)
    if iv is None:
        raise LookupError("干预记录不存在")
    base: dict = {
        "intervention_id": iv.id,
        "kind": iv.kind,
        "scope": iv.scope,
        "status": iv.status,
        "student_id": iv.student_id,
        "kp": graph.kp(iv.kp_id).name,
        "baseline_as_of": str(iv.baseline_as_of.date()),
    }
    if iv.status != "done":
        return {**base, "effect_status": "not_executed"}

    when = now or datetime.now()
    pre = mastery_at(session, iv.student_id, iv.kp_id, iv.baseline_as_of)
    pre_v = pre if pre is not None else 0.0
    post_window = [
        e
        for e in get_events(session, iv.student_id, iv.kp_id, when)
        if e.occurred_at > iv.done_at
    ]
    if not post_window:
        return {**base, "effect_status": "awaiting_retest", "pre_mastery": round(pre_v, 3)}

    last_t = max(e.occurred_at for e in post_window)
    post = mastery_at(session, iv.student_id, iv.kp_id, last_t)
    delta = (post - pre_v) if post is not None else 0.0

    # 基线调整：同 kp 班级同期平均变化，扣除向均值回归（薄弱点按低位选出，
    # 不调整会系统性高估）。班级数据不足回落 raw delta 并显式标注。
    class_delta = _class_window_delta(session, iv, iv.baseline_as_of, last_t)
    adjusted = delta - class_delta if class_delta is not None else delta
    if adjusted >= INTERVENTION_MIN_DELTA:
        verdict = "improved"
    elif adjusted >= INTERVENTION_FLAT_FLOOR:
        verdict = "flat"
    else:
        verdict = "declined"

    out = {
        **base,
        "effect_status": verdict,
        "pre_mastery": round(pre_v, 3),
        "post_mastery": round(post, 3) if post is not None else None,
        "delta": round(delta, 3),
        "evaluated_at": str(last_t.date()),
    }
    if class_delta is not None:
        out["class_delta"] = round(class_delta, 3)
        out["adjusted_delta"] = round(adjusted, 3)
    else:
        out["adjusted_note"] = "班级数据不足，未做基线调整（原始差值）"
    return out


def _class_window_delta(
    session: Session,
    iv: Intervention,
    start: datetime,
    end: datetime,
) -> float | None:
    """同窗口该 kp 的班级平均掌握度变化（基线调整项）。样本 <4 返回 None。"""
    peers = list(
        session.scalars(select(Student.id).where(Student.class_id == iv.class_id))
    )
    deltas: list[float] = []
    for pid in peers:
        if pid == iv.student_id:
            continue
        m_pre = mastery_at(session, pid, iv.kp_id, start)
        m_post = mastery_at(session, pid, iv.kp_id, end)
        if m_pre is not None and m_post is not None:
            deltas.append(m_post - m_pre)
    if len(deltas) < 4:
        return None
    return sum(deltas) / len(deltas)


# ---------------------------------------------------------------------------
# 闭环度量（§4 北极星）：采纳率 + 干预提升率
# ---------------------------------------------------------------------------


def intervention_summary(session: Session, graph: KpGraph, class_id: int) -> dict:
    """闭环度量（照抄 attribution_closure 形状）。分母口径：提升率只算可评估子集。"""
    rows = list(
        session.scalars(select(Intervention).where(Intervention.class_id == class_id))
    )
    by_status: dict[str, int] = {"suggested": 0, "done": 0, "skipped": 0}
    by_kind: dict[str, int] = {}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1

    done_rows = [r for r in rows if r.status == "done"]
    skipped_n = by_status.get("skipped", 0)
    adopt_denom = len(done_rows) + skipped_n

    dist = {"awaiting_retest": 0, "improved": 0, "flat": 0, "declined": 0}
    by_kind_effect: dict[str, dict[str, int]] = {}
    for r in done_rows:
        e = intervention_effect(session, graph, r.id)
        es = e["effect_status"]
        slot = by_kind_effect.setdefault(
            e["kind"], {"awaiting_retest": 0, "improved": 0, "flat": 0, "declined": 0}
        )
        if es in dist:
            dist[es] += 1
            slot[es] += 1

    evaluable = dist["improved"] + dist["flat"] + dist["declined"]
    lift_rate = round(dist["improved"] / evaluable, 3) if evaluable else None
    adoption = round(len(done_rows) / adopt_denom, 3) if adopt_denom else None

    return {
        "total": len(rows),
        "by_status": by_status,
        "by_kind": by_kind,
        "adoption_rate": adoption,
        "effects": dist,
        # 北极星指标「干预提升率」（README 兑现）：首期不断言阈值，先度量后校准
        "intervention_lift_rate": lift_rate,
        "evaluable_count": evaluable,
        "by_kind_effect": by_kind_effect,
    }


# ---------------------------------------------------------------------------
# 行动方向读视图（端点用）：三层杠杆排序 + 渲染所需字段
# ---------------------------------------------------------------------------


def action_plan_view(
    session: Session, graph: KpGraph, class_id: int, exam_id: int | None = None
) -> dict:
    """教学行动方向结构化数据（GET /classes/{id}/action-plan 数据源）。

    三层杠杆降序：全班重讲（一次课覆盖所有人）→ 小组（人数降序）→ 个体
    （K5 重要度：基础>核心>拓展，同级按掌握度缺口降序）。名单原序，无排名。
    """
    stmt = select(Intervention).where(Intervention.class_id == class_id)
    if exam_id is not None:
        stmt = stmt.where(Intervention.exam_id == exam_id)
    rows = list(session.scalars(stmt))

    covered = covered_kp_ids(session, class_id, None)

    def _imp(kp_id: int) -> int:
        return {"基础": 0, "核心": 1, "拓展": 2}.get(graph.kp(kp_id).importance, 1)

    def _gap_key(r: Intervention) -> float:
        if r.student_id is None:
            return 0.0
        m = mastery_at(session, r.student_id, r.kp_id, r.baseline_as_of)
        return -(1.0 - (m if m is not None else 1.0))

    class_rows = [r for r in rows if r.scope == SCOPE_CLASS]
    group_rows = [r for r in rows if r.scope == SCOPE_GROUP]
    student_rows = [r for r in rows if r.scope == SCOPE_STUDENT]

    group_size: dict[str, int] = {}
    for r in group_rows:
        group_size[r.group_ref] = group_size.get(r.group_ref, 0) + 1
    group_rows.sort(key=lambda r: (-group_size.get(r.group_ref, 0), r.group_ref or ""))
    student_rows.sort(key=lambda r: (_imp(r.kp_id), _gap_key(r)))

    def _serialize(r: Intervention) -> dict:
        kp = graph.kp(r.kp_id)
        d: dict = {
            "id": r.id,
            "kind": r.kind,
            "scope": r.scope,
            "status": r.status,
            "kp_code": kp.code,
            "kp_name": kp.name,
            "note": r.note,
            "suggested_at": r.suggested_at.isoformat() if r.suggested_at else None,
            "done_at": r.done_at.isoformat() if r.done_at else None,
            "taught": r.kp_id in covered,
        }
        if r.scope == SCOPE_GROUP:
            d["group_size"] = group_size.get(r.group_ref, 0)
        if r.student_id is not None:
            stu = session.get(Student, r.student_id)
            d["student_id"] = r.student_id
            d["alias"] = stu.name_or_alias if stu else None
        return d

    return {
        "class_id": class_id,
        "exam_id": exam_id,
        "pending_confirm": sum(1 for r in rows if r.status == "suggested"),
        "rows": [_serialize(r) for r in [*class_rows, *group_rows, *student_rows]],
        "counts": {
            "class": len(class_rows),
            "group": len(group_rows),
            "student": len(student_rows),
        },
    }
