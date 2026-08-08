"""证据派生规则（DESIGN §6）：猜测校正 / 级联降权 / 来源权重 / 分摊 / 不变量①。"""

from __future__ import annotations

from datetime import date

import pytest

from app.ingestion.commit import commit_exam
from app.models import ExamResponse, ResponseAnswer
from app.pipeline.evidence import derive_events_for_response
from app.models import EvidenceEvent
from sqlalchemy import select
from tests.conftest import make_exam, add_progress


def _answer(session, tpl, student_id, scores, cascade=False):
    resp = ExamResponse(exam_template_id=tpl.id, student_id=student_id,
                        source="excel", status="待审核")
    session.add(resp)
    session.flush()
    for q, score in zip(tpl.questions, scores):
        session.add(ResponseAnswer(exam_response_id=resp.id,
                                   template_question_id=q.id, score=score,
                                   cascade_flag=cascade))
    resp.total_score = sum(scores)
    session.flush()
    return resp


def test_choice_guess_correction(session, env):
    kp = env["kp"]["U"]
    tpl = make_exam(session, env["class"].id, "测1", date(2025, 9, 10), "单元",
                    [(1, 5.0, "选择", "识记", [(kp, 1.0)])])
    resp = _answer(session, tpl, env["students"]["T01"], [0.0])
    resp.status = "已提交"
    session.flush()
    derive_events_for_response(session, resp.id)
    ev = session.scalar(select(EvidenceEvent))
    assert ev.value == 0.0  # 答错：max(0, (0-0.25)/0.75) = 0


def test_cascade_halves_weight(session, env):
    kp = env["kp"]["U"]
    tpl = make_exam(session, env["class"].id, "测2", date(2025, 9, 11), "期中",
                    [(1, 10.0, "解答", "应用", [(kp, 1.0)])])
    resp = _answer(session, tpl, env["students"]["T01"], [5.0], cascade=True)
    resp.status = "已提交"
    session.flush()
    derive_events_for_response(session, resp.id)
    ev = session.scalar(select(EvidenceEvent))
    assert ev.weight == pytest.approx(1.0 * 0.5)      # 期中权重 1.0 × 级联 0.5
    assert ev.value == pytest.approx(0.5)


def test_kp_weight_split(session, env):
    """一题两知识点 0.6/0.4 → 两条事件，权重按分摊。"""
    p1, p2 = env["kp"]["P1"], env["kp"]["P2"]
    tpl = make_exam(session, env["class"].id, "测3", date(2025, 9, 12), "期中",
                    [(1, 10.0, "解答", "应用", [(p1, 0.6), (p2, 0.4)])])
    resp = _answer(session, tpl, env["students"]["T01"], [8.0])
    resp.status = "已提交"
    session.flush()
    derive_events_for_response(session, resp.id)
    evs = list(session.scalars(select(EvidenceEvent)))
    assert len(evs) == 2
    by_kp = {e.kp_id: e for e in evs}
    assert by_kp[p1].weight == pytest.approx(0.6)
    assert by_kp[p2].weight == pytest.approx(0.4)
    assert all(e.value == pytest.approx(0.8) for e in evs)


def test_source_weight_practice(session, env):
    kp = env["kp"]["U"]
    tpl = make_exam(session, env["class"].id, "练1", date(2025, 9, 13), "练习",
                    [(1, 5.0, "解答", "应用", [(kp, 1.0)])])
    resp = _answer(session, tpl, env["students"]["T01"], [5.0])
    resp.status = "已提交"
    session.flush()
    derive_events_for_response(session, resp.id)
    assert session.scalar(select(EvidenceEvent)).weight == pytest.approx(0.5)


def test_uncommitted_rejected(session, env):
    """架构不变量①：未提交数据不得派生证据。"""
    kp = env["kp"]["U"]
    tpl = make_exam(session, env["class"].id, "测4", date(2025, 9, 14), "单元",
                    [(1, 5.0, "解答", "应用", [(kp, 1.0)])])
    resp = _answer(session, tpl, env["students"]["T01"], [5.0])
    with pytest.raises(ValueError, match="不变量①"):
        derive_events_for_response(session, resp.id)


def test_idempotent_derivation(session, env):
    kp = env["kp"]["U"]
    tpl = make_exam(session, env["class"].id, "测5", date(2025, 9, 15), "单元",
                    [(1, 5.0, "解答", "应用", [(kp, 1.0)])])
    _answer(session, tpl, env["students"]["T01"], [5.0])
    commit_exam(session, tpl.id)
    n1 = len(list(session.scalars(select(EvidenceEvent))))
    # 重复对同一已提交作答派生 → 不新增
    resp = session.scalar(select(ExamResponse))
    assert derive_events_for_response(session, resp.id) == 0
    assert len(list(session.scalars(select(EvidenceEvent)))) == n1
