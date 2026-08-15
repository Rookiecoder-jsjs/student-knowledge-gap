"""高杠杆参数影响估算（架构修复 候选2：从 routes 抽出的 kb 预览逻辑）。

改 mastery_floor / difficulty_prior 时，预览「会有多少学生因此改变薄弱判定」。
纯阈值比较，不含教学进度/证据数门槛（粗略量级，与 kb-edit §4.3 preview 语义一致）。
"""

from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EvidenceEvent
from app.pipeline.mastery import mastery_at


def _day_end() -> datetime:
    """本地今日末（证据截止；deps._as_dt 同一约定——utcnow 会漏当天上午证据）。"""
    return datetime.combine(datetime.now().date(), time(23, 59))


def weak_count_for_kp(session: Session, kp_id: int, floor: float) -> int:
    """该 kp 有证据的学生中，掌握度 < floor 的数量。"""
    count = 0
    for sid in session.scalars(
        select(EvidenceEvent.student_id).where(EvidenceEvent.kp_id == kp_id).distinct()
    ):
        m = mastery_at(session, sid, kp_id, _day_end())
        if m is not None and m < floor:
            count += 1
    return count


def floor_impact(
    session: Session, kp_id: int, current_floor: float, projected_floor: float
) -> dict:
    """current vs projected 的薄弱人数影响。返回 ``{current, projected, delta}``。"""
    cur = weak_count_for_kp(session, kp_id, current_floor)
    proj = weak_count_for_kp(session, kp_id, projected_floor)
    return {
        "current": {"weak_count": cur, "floor": round(current_floor, 4)},
        "projected": {"weak_count": proj, "floor": round(projected_floor, 4)},
        "delta": proj - cur,
    }