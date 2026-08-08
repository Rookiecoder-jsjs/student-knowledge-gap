"""掌握度推导 — derive-on-read（DESIGN §6，架构不变量②）。

M(s,k,t) = Σⱼ eⱼ·wⱼ·2^(−Δtⱼ/hⱼ) / Σⱼ wⱼ·2^(−Δtⱼ/hⱼ)

- 半衰期 h 按证据来源类型（考试类 60 天 / 练习类 30 天）；
- 不存储可变快照：教师修正分数 → 重新提交 → 证据重建，此处自动正确；
- 支持按认知层级分维（归因"层级断层"的数据前提）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import HALF_LIFE_DAYS
from app.models import EvidenceEvent


def get_events(
    session: Session,
    student_id: int,
    kp_id: int,
    as_of: datetime,
    cog_level: str | None = None,
) -> list[EvidenceEvent]:
    """该学生该知识点截至 as_of 的全部证据事件（时间升序）。"""
    stmt = (
        select(EvidenceEvent)
        .where(
            EvidenceEvent.student_id == student_id,
            EvidenceEvent.kp_id == kp_id,
            EvidenceEvent.occurred_at <= as_of,
        )
        .order_by(EvidenceEvent.occurred_at)
    )
    if cog_level is not None:
        stmt = stmt.where(EvidenceEvent.cog_level == cog_level)
    return list(session.scalars(stmt))


def get_events_batch(
    session: Session,
    student_ids,
    kp_ids,
    as_of: datetime,
    cog_level: str | None = None,
) -> dict[tuple[int, int], list[EvidenceEvent]]:
    """G4：一次查询取多学生×多知识点的证据事件，内存按 (student_id, kp_id) 分组。

    与 get_events 等价（同过滤、同 occurred_at 升序），仅把 N×M 次查询压成 1 次。
    供 assess_student_kps 预取全班×全 kp 事件，master_of_events 在内存上计算。
    """
    sids = list(student_ids)
    kids = list(kp_ids)
    if not sids or not kids:
        return {}
    stmt = (
        select(EvidenceEvent)
        .where(
            EvidenceEvent.student_id.in_(sids),
            EvidenceEvent.kp_id.in_(kids),
            EvidenceEvent.occurred_at <= as_of,
        )
        .order_by(
            EvidenceEvent.student_id,
            EvidenceEvent.kp_id,
            EvidenceEvent.occurred_at,
        )
    )
    if cog_level is not None:
        stmt = stmt.where(EvidenceEvent.cog_level == cog_level)
    out: dict[tuple[int, int], list[EvidenceEvent]] = {}
    for ev in session.scalars(stmt):
        out.setdefault((ev.student_id, ev.kp_id), []).append(ev)
    return out


def mastery_of_events(
    events: list[EvidenceEvent],
    as_of: datetime,
    prior: float | None = None,
    prior_strength: float = 5.0,
) -> float | None:
    """由事件序列推导掌握度；无事件返回 None（与 0 区分：0=确定差，None=无数据）。

    prior 给定时做贝叶斯收缩（kb-improvement-design K7-A）：
        mastery = (likelihood·n + prior·prior_strength) / (n + prior_strength)
    数据少（n 小）时偏向先验，数据多时回归观测。prior_strength=0 等价纯观测。
    """
    if not events:
        return None
    num = den = 0.0
    for ev in events:
        delta_days = max(0.0, (as_of - ev.occurred_at).total_seconds() / 86400.0)
        half_life = HALF_LIFE_DAYS.get(ev.source_type, 60.0)
        decay = math.pow(2.0, -delta_days / half_life)
        w = ev.weight * decay
        num += ev.value * w
        den += w
    likelihood = num / den if den > 0 else None
    if likelihood is None:
        return None
    if prior is None or prior_strength <= 0:
        return likelihood
    n = float(len(events))
    return (likelihood * n + prior * prior_strength) / (n + prior_strength)


def mastery_at(
    session: Session,
    student_id: int,
    kp_id: int,
    as_of: datetime,
    cog_level: str | None = None,
) -> float | None:
    return mastery_of_events(get_events(session, student_id, kp_id, as_of, cog_level), as_of)


@dataclass
class EvidenceSummary:
    count: int
    last_at: datetime | None
    last_source_type: str | None


def evidence_summary(
    session: Session, student_id: int, kp_id: int, as_of: datetime
) -> EvidenceSummary:
    events = get_events(session, student_id, kp_id, as_of)
    return EvidenceSummary(
        count=len(events),
        last_at=events[-1].occurred_at if events else None,
        last_source_type=events[-1].source_type if events else None,
    )


def mastery_series(
    session: Session, student_id: int, kp_id: int, as_of: datetime
) -> list[tuple[datetime, float]]:
    """历史掌握度轨迹：在每个证据时刻重推 M(t)。

    用途：遗忘衰减检测（曾高今低）、轨迹形态分类。
    """
    events = get_events(session, student_id, kp_id, as_of)
    series: list[tuple[datetime, float]] = []
    for i, ev in enumerate(events):
        m = mastery_of_events(events[: i + 1], ev.occurred_at)
        if m is not None and (not series or series[-1][0] != ev.occurred_at):
            series.append((ev.occurred_at, m))
        elif m is not None and series and series[-1][0] == ev.occurred_at:
            series[-1] = (ev.occurred_at, m)  # 同日多事件取最后一次
    return series
