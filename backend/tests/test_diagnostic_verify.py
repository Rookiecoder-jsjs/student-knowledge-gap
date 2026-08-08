"""诊断题证伪闭环（improvement-plan §1.4-A）。

诊断题（type=诊断、单 kp）作答提交后派生单 kp 证据；verify_attribution_prediction
用该证据验证前置缺陷归因的预测：前置点已达标 -> 证伪 -> overridden（跨重跑保留）。
"""

from __future__ import annotations

from datetime import date, datetime

from app.ingestion.commit import commit_exam
from app.kb.graph import KpGraph
from app.models import Attribution, ExamResponse, ResponseAnswer
from app.pipeline.attribution import ATTR_PREREQ, verify_attribution_prediction
from tests.conftest import make_exam


def _answer(session, tpl, student_id, per_q):
    resp = ExamResponse(
        exam_template_id=tpl.id, student_id=student_id, source="excel", status="待审核"
    )
    session.add(resp)
    session.flush()
    for q in tpl.questions:
        session.add(
            ResponseAnswer(
                exam_response_id=resp.id,
                template_question_id=q.id,
                score=per_q.get(q.idx, 0.0),
            )
        )
    resp.total_score = sum(per_q.values())
    session.flush()
    return resp


def _diagnostic_on_kp(session, env, kp_id, student_id, score, n=3):
    """n 场诊断考试（单 kp 题），学生得分固定 -> 掌握度 ≈ score/10。"""
    for i in range(n):
        tpl = make_exam(
            session,
            env["class"].id,
            f"诊断{i}",
            date(2025, 10, 1 + i * 10),
            "诊断",
            [(1, 10.0, "解答", "应用", [(kp_id, 1.0)])],
        )
        _answer(session, tpl, student_id, {1: score})
        commit_exam(session, tpl.id)


def test_verify_falsified_when_root_mastered(session, env):
    """前置点诊断题高分 -> 掌握度达标 -> 证伪 -> overridden。"""
    p1, p3 = env["kp"]["P1"], env["kp"]["P3"]
    s = env["students"]["T01"]
    graph = KpGraph(session, env["kb"].id)
    when = datetime(2025, 11, 1, 12, 0)

    _diagnostic_on_kp(session, env, p1, s, 9.0)  # 掌握度 ≈ 0.9
    att = Attribution(
        student_id=s,
        kp_id=p3,
        type=ATTR_PREREQ,
        root_kp_id=p1,
        confidence=0.8,
        prediction="若前置缺陷，P1 诊断题应低于 60%",
        status="active",
    )
    session.add(att)
    session.flush()

    r = verify_attribution_prediction(session, graph, att.id, when)
    assert r["verdict"] == "falsified"
    assert r["root_mastery"] >= 0.6
    assert r["status"] == "overridden"
    assert "证伪" in r["note"]
    assert session.get(Attribution, att.id).status == "overridden"


def test_verify_supported_when_root_still_low(session, env):
    """前置点诊断题低分 -> 掌握度仍低 -> 证实 -> 保留 active。"""
    p1, p3 = env["kp"]["P1"], env["kp"]["P3"]
    s = env["students"]["T02"]
    graph = KpGraph(session, env["kb"].id)
    when = datetime(2025, 11, 1, 12, 0)

    _diagnostic_on_kp(session, env, p1, s, 2.0)  # 掌握度 ≈ 0.2
    att = Attribution(
        student_id=s,
        kp_id=p3,
        type=ATTR_PREREQ,
        root_kp_id=p1,
        confidence=0.8,
        prediction="若前置缺陷，P1 诊断题应低于 60%",
        status="active",
    )
    session.add(att)
    session.flush()

    r = verify_attribution_prediction(session, graph, att.id, when)
    assert r["verdict"] == "supported"
    assert r["root_mastery"] < 0.6
    assert r["status"] == "active"
    assert "证实" in r["note"]


def test_verify_inconclusive_insufficient_evidence(session, env):
    """诊断证据不足 -> inconclusive，状态不变。"""
    p1, p3 = env["kp"]["P1"], env["kp"]["P3"]
    s = env["students"]["T01"]
    graph = KpGraph(session, env["kb"].id)
    when = datetime(2025, 11, 1, 12, 0)

    _diagnostic_on_kp(session, env, p1, s, 9.0, n=1)  # 仅 1 题 < 3 门槛
    att = Attribution(
        student_id=s,
        kp_id=p3,
        type=ATTR_PREREQ,
        root_kp_id=p1,
        confidence=0.8,
        prediction="...",
        status="active",
    )
    session.add(att)
    session.flush()

    r = verify_attribution_prediction(session, graph, att.id, when)
    assert r["verdict"] == "inconclusive"
    assert r["status"] == "active"
    assert "不足" in r["note"]
