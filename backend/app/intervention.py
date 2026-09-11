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
    StudyRecord,
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

    合并与归档纪律（行动明细队列化，§1.2 生成端前提）：
    - 同 (班级, 学生, 点) 至多一条挂起建议，跨考试原位刷新不新增（存量同键
      重复只留最新一条，其余归档）；
    - 前提消失的挂起建议随本场提交自动归档（学生达标 / 对症归因失效 / 班级
      共性占比跌破阈值且样本充足）。

    返回 {"suggested": 物化行数（含刷新）, "groups": 成组数, "archived": 归档数}。
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

    # ---- 跨考试合并（行动明细队列化的生成端前提）：同 (班级, 学生, KP)
    # 至多一条挂起建议——已存在则原行刷新（换基线/注记/对症动作/组标记），
    # 不新增事实。kind 不进键：同一需求的对症动作随最新归因演进，刷新而非
    # 并存。积压数从此随复测/达标收缩，采纳率分母不再被重复建议污染。
    pending_grouped: dict[tuple[int | None, int], list[Intervention]] = {}
    for r in session.scalars(
        select(Intervention).where(
            Intervention.class_id == class_id,
            Intervention.status == "suggested",
        )
    ):
        pending_grouped.setdefault((r.student_id, r.kp_id), []).append(r)
    # 合并纪律生效前的存量同键重复只留最新一条，其余归档（事实保留）
    pending_by_key: dict[tuple[int | None, int], Intervention] = {}
    for key, group in pending_grouped.items():
        group.sort(key=lambda r: r.id)
        for dup in group[:-1]:
            dup.status = "skipped"
            dup.note = "系统合并：同一需求仅保留最新建议"
        pending_by_key[key] = group[-1]

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
    still_ask: dict[int, set[int]] = {}  # 本场评估仍支持行动建议的知识点集
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
        still_ask[sid] = {r.kp_id for r in kept_rows}
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

    # 班级行自动归档护栏：仅当该点本场被足够学生评估（n≥4）且共性占比跌破
    # 阈值——「证明已消退」才归档；样本不足（n<4）不下结论，保留原建议。
    class_common_now = {r.kp_id for r in class_rows}
    disproven_kps = {
        kp
        for kp, c in weak_count.items()
        if kp not in class_common_now
        and n_assessed.get(kp, 0) >= 4
        and c / n_assessed[kp] < CLASS_COMMON_WEAK_RATIO
    }

    # ---- 物化落库（合并优先：已有挂起行原位刷新，事实不重复）----
    created = 0

    def _upsert(row: ActionRow, sid: int | None) -> None:
        nonlocal created
        existing = pending_by_key.get((sid, row.kp_id))
        if existing is not None:
            existing.exam_id = exam_id
            existing.source_report_id = source_report_id
            existing.kind = row.kind
            existing.scope = row.scope
            existing.group_ref = (
                f"r{source_report_id or 0}:{row.root_kp_id}"
                if row.scope == SCOPE_GROUP
                else None
            )
            existing.baseline_as_of = as_of
            existing.note = (
                f"{row.note}，建议下节课前 15 分钟重讲 + 变式训练"
                if sid is None
                else row.note
            )
            created += 1
            return
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
                baseline_as_of=as_of, status="suggested",
                note=(
                    f"{row.note}，建议下节课前 15 分钟重讲 + 变式训练"
                    if sid is None
                    else row.note
                ),
            )
        )
        created += 1

    for row in class_rows:
        _upsert(row, None)
    for sid in committed_ids:
        for row in individual[sid]:
            _upsert(row, sid)

    # ---- 自动归档：前提消失的挂起建议随本场提交落 skipped（系统注记）。
    # 学生侧：该生本场评估已不再支持此建议（已达标 / 对症归因失效）；
    # 班级侧：共性占比跌破阈值且样本充足。归档是事实追加，不删历史；
    # 日后若再次薄弱，下一场考试会照常重新建议（自愈）。
    archived = 0
    for (sid, kp), existing in pending_by_key.items():
        if existing.status != "suggested":
            continue
        gone = (
            sid is None and kp in disproven_kps
        ) or (
            sid is not None
            and sid in still_ask
            and kp not in still_ask[sid]
        )
        if gone:
            existing.status = "skipped"
            existing.note = "系统归档：最新评估不再支持此建议"
            archived += 1

    session.flush()
    return {"suggested": created, "groups": group_count, "archived": archived}


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

    # 自报待检验（study-loop-design）：有自报事实的学生逐一折叠计数——
    # 软闭合量（老师零操作、被动验证），与 done 行计数口径互补
    self_reported = 0
    reporters = list(
        session.scalars(
            select(StudyRecord.student_id)
            .where(
                StudyRecord.class_id == class_id,
                StudyRecord.self_marked_at.is_not(None),
            )
            .distinct()
        )
    )
    if reporters:
        # 局部 import 防循环（progress 顶层引用本模块阈值常量，同 action_plan_view）
        from app.pipeline.progress import LOOP_SELF_REPORTED, loop_states_for_student

        now = datetime.now()
        for sid in reporters:
            states = loop_states_for_student(session, graph, sid, class_id, now)
            self_reported += sum(
                1 for v in states.values() if v == LOOP_SELF_REPORTED
            )

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
        # 学生自报闭环量：已自报待下一场考试被动验证的 (学生, 知识点) 数
        "self_reported": self_reported,
    }


# ---------------------------------------------------------------------------
# 行动方向读视图（端点用）：三层杠杆排序 + 渲染所需字段
# ---------------------------------------------------------------------------

# 行动明细队列上限（§1.2 收口）：待办超过 10 条对教师不是清单是噪音——
# 排序责任在系统（杠杆序截前 10），完整事实走 /interventions 列表。
ACTION_QUEUE_MAX = 10


def action_plan_view(
    session: Session, graph: KpGraph, class_id: int, exam_id: int | None = None
) -> dict:
    """教学行动方向结构化数据（GET /classes/{id}/action-plan 数据源）。

    三层杠杆降序：全班重讲（一次课覆盖所有人）→ 小组（人数降序）→ 个体
    （K5 重要度：基础>核心>拓展，同级按掌握度缺口降序）。名单原序，无排名。

    rows 是**待办队列**而非账本投影：仅挂起建议、班级行覆盖的个体/小组行
    折叠隐藏（视图规则，事实不动）、小组按组一行、截前 ACTION_QUEUE_MAX 条。
    pending_confirm / counts 保持全量事实口径（积压数诚实展示）。
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
    class_rows.sort(key=lambda r: (_imp(r.kp_id), r.id))
    group_rows.sort(key=lambda r: (-group_size.get(r.group_ref, 0), r.group_ref or ""))
    student_rows.sort(key=lambda r: (_imp(r.kp_id), _gap_key(r)))

    # 行级进度状态（闭环一期 P1）：局部 import 防循环（progress 顶层引用本模块
    # 的阈值常量）；同一学生的多行共享一次折叠
    from app.pipeline.progress import (
        LOOP_INSUFFICIENT,
        LOOP_SUGGESTED,
        loop_states_for_student,
        row_loop_state,
    )

    loop_cache: dict[int, dict[int, str]] = {}

    def _loop(sid: int) -> dict[int, str]:
        if sid not in loop_cache:
            loop_cache[sid] = loop_states_for_student(
                session, graph, sid, class_id, datetime.now()
            )
        return loop_cache[sid]

    def _serialize(r: Intervention) -> dict:
        kp = graph.kp(r.kp_id)
        d: dict = {
            "id": r.id,
            "kind": r.kind,
            "scope": r.scope,
            "status": r.status,
            "group_ref": r.group_ref,
            "kp_code": kp.code,
            "kp_name": kp.name,
            "note": r.note,
            "loop_state": (
                _loop(r.student_id).get(r.kp_id)
                if r.student_id is not None
                else row_loop_state(r)
            ),
            "retest_exam_id": r.retest_exam_id,
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

    serialized = [_serialize(r) for r in [*class_rows, *group_rows, *student_rows]]

    # ---- 待办队列折叠（账本 → 队列，§1.2 收口）----
    # ①学生级行过折叠态门槛：诉求仍成立（已建议/证据不足）才进队列，
    #   达标/待复测/已闭合等状态自动退出；
    # ②班级行覆盖抑制：同点已有挂起的全班重讲，个体/小组行不再是独立待办
    #   （班级行被跳过或复测判未闭合后自动回流——纯视图规则，事实不动）；
    # ③小组行按 group_ref 折叠一行（确认/跳过按组批量落事实）；
    # ④截前 ACTION_QUEUE_MAX 条，无「展开全部」。
    queue_include = {LOOP_SUGGESTED, LOOP_INSUFFICIENT}
    class_pending_codes = {
        s["kp_code"]
        for s in serialized
        if s["scope"] == SCOPE_CLASS and s["status"] == "suggested"
    }

    def _standalone(s: dict) -> bool:
        return (
            s["loop_state"] in queue_include
            and s["kp_code"] not in class_pending_codes
        )

    seen_groups: set[str] = set()
    candidates: list[dict] = []
    for s in serialized:
        if s["status"] != "suggested":
            continue
        if s["scope"] in (SCOPE_GROUP, SCOPE_STUDENT):
            if not _standalone(s):
                continue
            if s["scope"] == SCOPE_GROUP:
                ref = s.get("group_ref")
                if ref is None or ref in seen_groups:
                    continue  # 同组只出代表行
                seen_groups.add(ref)
        candidates.append(s)

    # ⑤同键去重（与生成端合并纪律同口径）：存量重复行只露最新一条，
    #   其余待下次提交被生成端归档——队列不重复呈现同一需求。
    best: dict[tuple, dict] = {}
    for s in candidates:
        key = (
            ("class", s["kp_code"])
            if s["scope"] == SCOPE_CLASS
            else ("student", s.get("student_id"), s["kp_code"])
        )
        prev = best.get(key)
        if prev is None or s["id"] > prev["id"]:
            best[key] = s
    keep_ids = {s["id"] for s in best.values()}
    queue = [s for s in candidates if s["id"] in keep_ids][:ACTION_QUEUE_MAX]

    return {
        "class_id": class_id,
        "exam_id": exam_id,
        "pending_confirm": sum(1 for s in serialized if s["status"] == "suggested"),
        "rows": queue,
        "counts": {
            "class": len(class_rows),
            "group": len(group_rows),
            "student": len(student_rows),
        },
    }
