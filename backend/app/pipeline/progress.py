"""知识点进度生命周期折叠（progress-loop-design P1）。

状态 = 对现成事实（KpAssessment 门槛/薄弱判定 + Intervention 执行事实 +
done_at 后证据）的确定性折叠，**不存储任何状态**（守不变量②：教师改分 →
重提交 → 折叠自动正确）。三端共用同一真相：教师行级徽标 / 学生门户进度
故事 / MCP 对账工具。

折叠规则（每个 学生×知识点 一态）：
- 门槛优先：未学到 / 证据不足 → 同名状态（绝不判薄弱；自报修不了证据缺口）；
- 非薄弱 → 达标；
- 有 done 行：取最新 done_at，之后有证据 → 判决接管（自报被覆盖）：
  post(=mastery_at(last_t)) − pre(=mastery_at(baseline)) 按
  ``INTERVENTION_MIN_DELTA``/``INTERVENTION_FLAT_FLOOR`` 判
  已闭合 / 持平复评 / 未闭合——阈值与 intervention_effect 同源，
  班级基线调整同口径（<4 样本回落原始差值）；
- done 行之后无证据 → 该生该点当前轮已自报「我学会了」（study-loop-design：
  自报时间 >= 本轮建议行的 suggested_at）→ **自报待检验**（软闭合：老师零
  负担，下一场考试证据被动验证；谎报会被考试自然打回）；否则 → 待复测；
- 无 done 行：当前轮自报（其后无新证据、不早于本轮建议行创建）同样 → 自报待检验
  ——学生已自学的个体事实不被仍挂起的班级行掩盖（集体行动走教师队列视图）；
  否则任一行 suggested → 已建议；仅 skipped → 已跳过；无行 → 薄弱待干预。

班级 scope 行（student_id=None）对该 KP 上所有薄弱学生生效；集体行的
行级视图只给三态（已建议/待复测/已跳过），不下个体判决——个体闭合在
学生级视图呈现。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.intervention import INTERVENTION_FLAT_FLOOR, INTERVENTION_MIN_DELTA
from app.models import EvidenceEvent, Intervention, Student
from app.pipeline.mastery import get_events_batch, mastery_of_events
from app.pipeline.weakness import (
    GATE_INSUFFICIENT,
    GATE_NOT_LEARNED,
    KpAssessment,
    assess_student_kps,
)
from app.kb.graph import KpGraph

# 生命周期状态（封闭集合；前端标签经 labels_source 生成）
LOOP_NOT_LEARNED = "未学到"
LOOP_INSUFFICIENT = "证据不足"
LOOP_ON_TRACK = "达标"
LOOP_WEAK_NEW = "薄弱待干预"
LOOP_SUGGESTED = "已建议"
LOOP_AWAITING = "待复测"
# 软闭合（study-loop-design）：学生自报已学、掌握度未动，等下一场考试被动验证
LOOP_SELF_REPORTED = "自报待检验"
LOOP_CLOSED = "已闭合"
LOOP_FLAT = "持平复评"
LOOP_OPEN = "未闭合"
LOOP_SKIPPED = "已跳过"

LOOP_STATES = (
    LOOP_NOT_LEARNED,
    LOOP_INSUFFICIENT,
    LOOP_ON_TRACK,
    LOOP_WEAK_NEW,
    LOOP_SUGGESTED,
    LOOP_AWAITING,
    LOOP_SELF_REPORTED,
    LOOP_CLOSED,
    LOOP_FLAT,
    LOOP_OPEN,
    LOOP_SKIPPED,
)


def row_loop_state(row: Intervention) -> str:
    """集体行/行级三态视图：suggested→已建议、done→待复测、skipped→已跳过。

    done 集体行不下 improved/declined 判决（个体闭合见学生级视图）。
    """
    if row.status == "suggested":
        return LOOP_SUGGESTED
    if row.status == "done":
        return LOOP_AWAITING
    return LOOP_SKIPPED


def _verdict_from_events(
    events: list[EvidenceEvent],
    baseline: datetime,
    last_t: datetime,
    peers: list[int],
    student_id: int,
    kp_id: int,
    events_by_sk: dict[tuple[int, int], list[EvidenceEvent]],
) -> str:
    """复测效果判决：与 intervention_effect 同阈值同基线调整口径。

    pre/post 全部由预取事件现算（mastery_of_events 无先验 = mastery_at 同式），
    班级窗口均值对冲均值回归，样本 <4 回落原始差值。
    """
    pre = mastery_of_events(
        [e for e in events if e.occurred_at <= baseline], baseline
    )
    post = mastery_of_events([e for e in events if e.occurred_at <= last_t], last_t)
    pre_v = pre if pre is not None else 0.0
    delta = (post - pre_v) if post is not None else 0.0

    deltas: list[float] = []
    for pid in peers:
        if pid == student_id:
            continue
        pe = events_by_sk.get((pid, kp_id), [])
        m_pre = mastery_of_events([e for e in pe if e.occurred_at <= baseline], baseline)
        m_post = mastery_of_events([e for e in pe if e.occurred_at <= last_t], last_t)
        if m_pre is not None and m_post is not None:
            deltas.append(m_post - m_pre)
    adjusted = delta - (sum(deltas) / len(deltas)) if len(deltas) >= 4 else delta

    if adjusted >= INTERVENTION_MIN_DELTA:
        return LOOP_CLOSED
    if adjusted >= INTERVENTION_FLAT_FLOOR:
        return LOOP_FLAT
    return LOOP_OPEN


def loop_states_for_student(
    session: Session,
    graph: KpGraph,
    student_id: int,
    class_id: int,
    as_of: datetime,
    *,
    assessments: list[KpAssessment] | None = None,
    events_by_sk: dict[tuple[int, int], list[EvidenceEvent]] | None = None,
    interventions: list[Intervention] | None = None,
    self_reports: dict[tuple[int, int], datetime] | None = None,
) -> dict[int, str]:
    """该学生全部主年级知识点的进度状态（kp_id → 状态）。

    可选入参均为调用方预取共享（全班矩阵零 N+1）：assessments 来自
    assess_student_kps；events_by_sk 为全班×全 kp 证据事件（G4 形状）；
    interventions 为该班全部干预行（含班级 scope 行，按 kp 归属生效）；
    self_reports 为 (student_id, kp_id) → 最新自报时间（app.study.self_report_map，
    缺省时按需单查该生）。
    """
    if assessments is None:
        assessments = assess_student_kps(session, graph, student_id, class_id, as_of)
    if events_by_sk is None:
        kp_ids = [a.kp_id for a in assessments]
        class_student_ids = [
            sid
            for (sid,) in session.execute(
                select(Student.id).where(Student.class_id == class_id)
            ).all()
        ]
        events_by_sk = get_events_batch(session, class_student_ids, kp_ids, as_of)
    if interventions is None:
        interventions = list(
            session.scalars(
                select(Intervention).where(Intervention.class_id == class_id)
            )
        )
    if self_reports is None:
        from app.study import self_report_map

        self_reports = self_report_map(session, [student_id])

    rows_by_kp: dict[int, list[Intervention]] = {}
    for r in interventions:
        if r.student_id is None or r.student_id == student_id:
            rows_by_kp.setdefault(r.kp_id, []).append(r)

    peers = [
        sid
        for (sid,) in session.execute(
            select(Student.id).where(Student.class_id == class_id)
        ).all()
    ]

    out: dict[int, str] = {}
    for a in assessments:
        if a.gate == GATE_NOT_LEARNED:
            out[a.kp_id] = LOOP_NOT_LEARNED
            continue
        if a.gate == GATE_INSUFFICIENT:
            out[a.kp_id] = LOOP_INSUFFICIENT
            continue
        if not a.is_weak:
            out[a.kp_id] = LOOP_ON_TRACK
            continue

        rows = rows_by_kp.get(a.kp_id, [])
        events = events_by_sk.get((student_id, a.kp_id), [])
        sr = self_reports.get((student_id, a.kp_id))
        # 轮次锚点 = 影响该生的建议行中最晚的创建时间（轮次真正开始的时刻）。
        # 不用 baseline_as_of——那是「考试日 23:59」的评估约定时点，同日生成的
        # 建议会晚于当天自报，会把当前轮自报误判成旧轮。
        round_anchor = max(
            (r.suggested_at for r in rows if r.suggested_at is not None),
            default=None,
        )

        # 复测证据优先于自报：done 行之后有新证据 → 判决接管（自报被覆盖）。
        done_rows = [r for r in rows if r.status == "done" and r.done_at is not None]
        if done_rows:
            latest = max(done_rows, key=lambda r: r.done_at)
            post = [e for e in events if e.occurred_at > latest.done_at]
            if post:
                last_t = max(e.occurred_at for e in post)
                out[a.kp_id] = _verdict_from_events(
                    events, latest.baseline_as_of, last_t,
                    peers, student_id, a.kp_id, events_by_sk,
                )
                continue
            # 软闭合（study-loop-design）：当前轮自报 → 自报待检验，否则待复测。
            # 旧轮自报不伪装成新轮进度（二次干预自动要求重学重报）。
            if sr is not None and (round_anchor is None or sr >= round_anchor):
                out[a.kp_id] = LOOP_SELF_REPORTED
            else:
                out[a.kp_id] = LOOP_AWAITING
            continue

        # 无 done 行：自报（其后无新证据、不早于本轮建议行创建）同样软闭合——
        # 学生已自学的个体事实不被仍挂起的班级行掩盖（集体行动走教师队列视图）。
        if (
            sr is not None
            and not any(e.occurred_at > sr for e in events)
            and (round_anchor is None or sr >= round_anchor)
        ):
            out[a.kp_id] = LOOP_SELF_REPORTED
            continue

        if not rows:
            out[a.kp_id] = LOOP_WEAK_NEW
            continue
        if any(r.status == "suggested" for r in rows):
            out[a.kp_id] = LOOP_SUGGESTED
            continue
        out[a.kp_id] = LOOP_SKIPPED
    return out
