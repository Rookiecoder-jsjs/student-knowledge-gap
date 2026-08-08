"""掌握度推导：手算对照（DESIGN §6 公式）。"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models import EvidenceEvent
from app.pipeline.mastery import mastery_of_events, mastery_at


def _ev(value, weight, days_ago, source="期中", kp_id=1, as_of=None):
    as_of = as_of or datetime(2026, 1, 20, 12, 0)
    return EvidenceEvent(
        student_id=1, kp_id=kp_id, response_answer_id=1, source_type=source,
        value=value, weight=weight, occurred_at=as_of - timedelta(days=days_ago),
        algo_version="t",
    )


def test_decay_hand_computed():
    """60 天前的满分证据（半衰期 60）衰减为 0.5 权重。"""
    now = datetime(2026, 1, 20, 12, 0)
    events = [_ev(1.0, 1.0, 60, as_of=now), _ev(0.4, 1.0, 0, as_of=now)]
    m = mastery_of_events(events, now)
    assert abs(m - (1.0 * 0.5 + 0.4 * 1.0) / 1.5) < 1e-9
    assert abs(m - 0.6) < 1e-9


def test_half_life_by_source():
    """练习类半衰期 30 天：30 天前的练习证据权重减半。"""
    now = datetime(2026, 1, 20, 12, 0)
    events = [_ev(1.0, 1.0, 30, source="练习", as_of=now), _ev(0.0, 1.0, 0, source="练习", as_of=now)]
    m = mastery_of_events(events, now)
    assert abs(m - 0.5 / 1.5) < 1e-9


def test_weight_scaling():
    """权重不影响等值证据的均值，但影响不同值证据的加权。"""
    now = datetime(2026, 1, 20, 12, 0)
    events = [_ev(1.0, 3.0, 0, as_of=now), _ev(0.0, 1.0, 0, as_of=now)]
    assert abs(mastery_of_events(events, now) - 0.75) < 1e-9


def test_empty_returns_none():
    assert mastery_of_events([], datetime(2026, 1, 1)) is None


def test_mastery_at_filters_cog_level(session, env):
    kp = env["kp"]["U"]
    now = datetime(2026, 1, 20, 12, 0)
    session.add(EvidenceEvent(student_id=1, kp_id=kp, response_answer_id=1,
                              source_type="期中", value=1.0, weight=1.0,
                              cog_level="识记", occurred_at=now, algo_version="t"))
    session.add(EvidenceEvent(student_id=1, kp_id=kp, response_answer_id=2,
                              source_type="期中", value=0.2, weight=1.0,
                              cog_level="应用", occurred_at=now, algo_version="t"))
    session.flush()
    assert mastery_at(session, 1, kp, now, cog_level="识记") == 1.0
    assert mastery_at(session, 1, kp, now, cog_level="应用") == 0.2
    assert abs(mastery_at(session, 1, kp, now) - 0.6) < 1e-9
