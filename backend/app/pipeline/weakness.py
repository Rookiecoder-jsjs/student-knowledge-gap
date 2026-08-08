"""薄弱判定（DESIGN §6）：证据门槛 + 双基准 + 轨迹形态分类。

护栏顺序（先门槛后判定，绝不跳步）：
1. 教学进度未覆盖 -> 「未学到」，永不判薄弱；
2. 证据题目数 < 3 -> 「数据不足」，不参与判定与归因；
3. 最近证据 > 90 天 -> 打「可能已变化」标记（仍可判，但报告显著提示）；
4. 双基准：掌握度 < 绝对底线（按知识点可配置）或 < 班级 P25。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import (
    CLASS_COMMON_WEAK_RATIO,
    CLASS_PERCENTILE,
    COG_FLOOR_DEFAULTS,
    DEFAULT_MASTERY_FLOOR,
    EVIDENCE_LOW_WATERMARK,
    MASTERY_PRIOR_STRENGTH,
    MIN_EVIDENCE_COUNT,
    STALE_DAYS,
    TRAJECTORY_TREND_PER_MONTH,
    TRAJECTORY_VOLATILITY,
    WEAKNESS_MODE,
    WEAKNESS_P25_MARGIN,
)
from app.kb.graph import KpGraph
from app.models import EvidenceEvent, Student, TeachingProgress
from app.pipeline.mastery import get_events_batch, mastery_of_events

TRAJ_STABLE = "稳定"
TRAJ_RISING = "上升"
TRAJ_DECLINING = "下滑"
TRAJ_VOLATILE = "震荡"

GATE_NOT_LEARNED = "未学到"
GATE_INSUFFICIENT = "数据不足"


@dataclass
class KpAssessment:
    """单个学生 × 单个知识点的评估结果（报告与归因的统一入参）。"""

    kp_id: int
    kp_code: str
    kp_name: str
    mastery: float | None = None
    evidence_count: int = 0
    last_evidence_at: datetime | None = None
    gate: str | None = None            # None | 未学到 | 数据不足
    stale: bool = False                # 可能已变化
    trajectory: str | None = None      # 稳定|上升|下滑|震荡
    is_weak: bool = False
    weak_criterion: str | None = None  # 绝对底线 | 班级P25 | 两者
    floor: float = 0.6
    class_p25: float | None = None
    is_class_common: bool = False      # 班级共性问题（不向学生归责）
    low_evidence: bool = False         # 评估但依据较少（MIN <= count < EVIDENCE_LOW_WATERMARK）
    per_cog_mastery: dict[str, float] | None = None  # 按认知层级分层的掌握度（kb-improvement-design K7-B）


def effective_floor(kp) -> float:
    """薄弱绝对底线：显式标注优先；否则按期望认知层级主导派生（kb-improvement-design K2）。

    综合级底线低（0.55，高阶题 0.55 已是较好水平）、识记级底线高（0.70）。
    从最高层级向下找（[理解,应用] → 应用 0.60；[应用,综合] → 综合 0.55），
    认知层级缺失时回退全局默认。教师可逐 KP 显式覆盖 mastery_floor。
    """
    if kp.mastery_floor != DEFAULT_MASTERY_FLOOR:
        return kp.mastery_floor
    for cog in ("综合", "应用", "理解", "识记"):
        if cog in (kp.cog_levels_expected or []):
            return COG_FLOOR_DEFAULTS.get(cog, DEFAULT_MASTERY_FLOOR)
    return DEFAULT_MASTERY_FLOOR


def _mastery_prior(kp) -> float | None:
    """difficulty 先验：1-difficulty（难度低 → 先验掌握度高）；K7-A 关闭时返回 None。"""
    if MASTERY_PRIOR_STRENGTH <= 0:
        return None
    return 1.0 - kp.difficulty_prior


def covered_kp_ids(session: Session, class_id: int, as_of: datetime | None = None) -> set[int]:
    """该班教学进度已覆盖的知识点。

    as_of 给定时做时间感知：taught_at 晚于评估时点的视为「当时未学到」，
    避免学期中对尚未教学的章节做薄弱判定（DESIGN §6 护栏）。
    """
    stmt = select(TeachingProgress.kp_id).where(TeachingProgress.class_id == class_id)
    if as_of is not None:
        stmt = stmt.where(TeachingProgress.taught_at <= as_of.date())
    return set(session.scalars(stmt))


def classify_trajectory(events) -> str | None:
    """由证据序列直接分类（DESIGN §6 轨迹形态）。"""
    if not events:
        return None
    if len(events) < 2:
        return TRAJ_STABLE
    t0 = events[0].occurred_at
    xs = [(e.occurred_at - t0).total_seconds() / 86400.0 / 30.0 for e in events]
    ys = [e.value for e in events]
    slope = _slope(xs, ys)
    if slope is not None and slope > TRAJECTORY_TREND_PER_MONTH:
        return TRAJ_RISING
    if slope is not None and slope < -TRAJECTORY_TREND_PER_MONTH:
        return TRAJ_DECLINING
    if _std(ys) > TRAJECTORY_VOLATILITY:
        return TRAJ_VOLATILE
    return TRAJ_STABLE


def assess_student_kps(
    session: Session,
    graph: KpGraph,
    student_id: int,
    class_id: int,
    as_of: datetime,
    events_by_sk: dict[tuple[int, int], list[EvidenceEvent]] | None = None,
) -> list[KpAssessment]:
    """对一个学生的全部主年级知识点做门槛 + 薄弱判定。

    G4：一次预取全班×全 kp 证据事件，内存分组计算掌握度/分布（原逐 (student,kp)
    get_events 为 N+1，50×50 规模数千次查询）。``events_by_sk`` 由调用方预取传入可
    跨学生复用（quality_analysis 班级报告）；缺省则内部预取。不改不变量②：不存可变
    掌握度快照，mastery_of_events 仍为纯函数推导。
    """
    covered = covered_kp_ids(session, class_id, as_of)
    kp_ids = list(graph.grade7_kp_ids())
    class_student_ids = [
        sid
        for (sid,) in session.execute(
            select(Student.id).where(Student.class_id == class_id)
        ).all()
    ]
    if events_by_sk is None:
        events_by_sk = get_events_batch(session, class_student_ids, kp_ids, as_of)

    def events_for(sid: int, kpid: int) -> list[EvidenceEvent]:
        return events_by_sk.get((sid, kpid), [])

    results: list[KpAssessment] = []
    for kp_id in kp_ids:
        kp = graph.kp(kp_id)
        a = KpAssessment(
            kp_id=kp_id, kp_code=kp.code, kp_name=kp.name, floor=effective_floor(kp)
        )

        if kp_id not in covered:
            a.gate = GATE_NOT_LEARNED
            results.append(a)
            continue

        events = events_for(student_id, kp_id)
        a.evidence_count = len(events)
        if not events:
            a.gate = GATE_INSUFFICIENT
            results.append(a)
            continue

        a.last_evidence_at = events[-1].occurred_at
        a.trajectory = classify_trajectory(events)
        if a.last_evidence_at and (as_of - a.last_evidence_at).days > STALE_DAYS:
            a.stale = True

        if len(events) < MIN_EVIDENCE_COUNT:
            a.gate = GATE_INSUFFICIENT
            a.mastery = mastery_of_events(events, as_of)  # 供参考展示（不达门槛，不收缩）
            results.append(a)
            continue

        # 贝叶斯先验仅作用于 low_evidence（2 证据）——K7-A 的本意是"避免 2 证据极端值"；
        # 3 证据及以上（已可判定）保持纯观测，避免先验把贴边学生压过底线（全班达标不误报）。
        prior = _mastery_prior(kp) if len(events) < EVIDENCE_LOW_WATERMARK else None
        a.mastery = mastery_of_events(events, as_of, prior=prior, prior_strength=MASTERY_PRIOR_STRENGTH)
        if len(events) < EVIDENCE_LOW_WATERMARK:
            a.low_evidence = True  # 评估但依据较少（降门槛时的诚实性护栏）
        # 认知层级分层掌握度（kb-improvement-design K7-B）：多层期望的 KP 按证据 cog_level
        # 分维，揭示"能复述但不会用"（识记高/应用低）的层级断层。复用预取事件，零额外查询。
        expected = kp.cog_levels_expected or []
        if len(expected) >= 2:
            per_cog: dict[str, float] = {}
            for cog in expected:
                cog_evs = [e for e in events if e.cog_level == cog]
                if len(cog_evs) >= MIN_EVIDENCE_COUNT:
                    m = mastery_of_events(cog_evs, as_of)
                    if m is not None:
                        per_cog[cog] = round(m, 3)
            if per_cog:
                a.per_cog_mastery = per_cog
        results.append(a)

    # ---- 双基准判定（班级分布复用预取数据，按 kp 缓存，避免重复扫描） ----
    class_dist_cache: dict[int, list[float]] = {}

    def class_mastery_dist(kp_id: int) -> list[float]:
        """全班该知识点掌握度分布（仅通过门槛的学生）。G4：复用预取事件，按 kp 缓存。"""
        if kp_id in class_dist_cache:
            return class_dist_cache[kp_id]
        if kp_id not in covered:
            class_dist_cache[kp_id] = []
            return []
        values: list[float] = []
        kp_obj = graph.kp(kp_id)
        for sid in class_student_ids:
            evs = events_for(sid, kp_id)
            if len(evs) >= MIN_EVIDENCE_COUNT:
                # 与个体评估同尺度：仅 low_evidence（2 证据）收缩，3 证据及以上纯观测
                prior = _mastery_prior(kp_obj) if len(evs) < EVIDENCE_LOW_WATERMARK else None
                m = mastery_of_events(evs, as_of, prior=prior, prior_strength=MASTERY_PRIOR_STRENGTH)
                if m is not None:
                    values.append(m)
        class_dist_cache[kp_id] = values
        return values

    valid = [a for a in results if a.mastery is not None and a.gate is None]
    for a in valid:
        distribution = class_mastery_dist(a.kp_id)
        if len(distribution) >= 4:
            a.class_p25 = percentile(distribution, CLASS_PERCENTILE)

        below_floor = a.mastery < a.floor
        below_p25 = a.class_p25 is not None and a.mastery < a.class_p25
        if WEAKNESS_MODE == "strict":
            # strict（V4）：P25 判据仅在掌握度贴近底线时触发，消除"全班达标仍按相对位置误报"
            below_p25 = below_p25 and a.mastery < a.floor + WEAKNESS_P25_MARGIN
        if below_floor and below_p25:
            a.is_weak, a.weak_criterion = True, "两者"
        elif below_floor:
            a.is_weak, a.weak_criterion = True, "绝对底线"
        elif below_p25:
            a.is_weak, a.weak_criterion = True, "班级P25"

    # ---- 班级共性：薄弱学生占比 ≥ 阈值 -> 教学问题，不向学生归责 ----
    for a in valid:
        if not a.is_weak:
            continue
        distribution = class_mastery_dist(a.kp_id)  # 缓存命中
        if len(distribution) >= 4:
            weak = sum(1 for m in distribution if m < a.floor)
            a.is_class_common = (weak / len(distribution)) >= CLASS_COMMON_WEAK_RATIO

    return results


# ---------------------------------------------------------------------------
# 数值工具
# ---------------------------------------------------------------------------


def percentile(values: list[float], p: float) -> float:
    """线性插值百分位数（p ∈ [0,100]）。"""
    if not values:
        raise ValueError("空序列无百分数")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * p / 100.0
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return xs[int(pos)]
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def _slope(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / den


def _std(ys: list[float]) -> float:
    if len(ys) < 2:
        return 0.0
    my = sum(ys) / len(ys)
    return math.sqrt(sum((y - my) ** 2 for y in ys) / len(ys))
