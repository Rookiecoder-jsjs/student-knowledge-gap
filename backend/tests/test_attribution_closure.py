"""证伪闭环度量测试（effectiveness-validation-plan V3-度量）。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select

from app.kb.graph import KpGraph
from app.models import Attribution, ExamResponse, ResponseAnswer
from app.pipeline.attribution import (
    ATTR_PREREQ,
    attribution_closure,
    verify_attribution_prediction,
)
from app.ingestion.commit import commit_exam
from tests.conftest import make_exam


def _att(student_id, kp_id, note, status="active", root_kp_id=None):
    return Attribution(
        student_id=student_id, kp_id=kp_id, type=ATTR_PREREQ, confidence=0.8,
        root_kp_id=root_kp_id, evidence_json=[], prediction="测试预测",
        status=status, teacher_note=note,
    )


def test_closure_classification(session, env):
    """手设归因状态/notes，验证 closure 分类与计数。"""
    p1, p3 = env["kp"]["P1"], env["kp"]["P3"]
    s = env["students"]["T01"]
    rows = [
        _att(s, p3, None, "active"),                                          # 未验证
        _att(s, p3, "诊断题证实（2026-01-16）：前置点「P1」掌握度 0.40 仍低于阈值，假设成立", "active", p1),  # supported
        _att(s, p3, "诊断题证伪（2026-01-16）：前置点「P1」掌握度 0.80 已达标（≥0.6），前置缺陷假设不成立", "overridden", p1),  # falsified
        _att(s, p3, "诊断证据不足（1 题 < 3），无法证伪（2026-01-16）", "active", p1),  # inconclusive
        _att(s, p3, "教师人工否决：归因不合理", "overridden"),                           # 人工否决
        _att(s, p3, None, "resolved"),                                        # 已解决
    ]
    for r in rows:
        session.add(r)
    session.flush()

    stats = attribution_closure(session, env["class"].id)
    print(f"\n[closure] {stats}")
    assert stats["total"] == 6
    assert stats["by_status"]["active"] == 3      # 未验证 + supported + inconclusive
    assert stats["by_status"]["overridden"] == 2  # falsified + 人工否决
    assert stats["by_status"]["resolved"] == 1
    assert stats["by_verdict"]["supported"] == 1
    assert stats["by_verdict"]["falsified"] == 1
    assert stats["by_verdict"]["inconclusive"] == 1
    assert stats["diagnostic_verified"] == 3      # supported + falsified + inconclusive
    assert stats["teacher_overridden"] == 1       # overridden 2 - falsified 1
    assert stats["closure_rate"] == round(3 / 6, 3)


def test_closure_after_real_verify(session, env):
    """真实 verify 流程后，closure 正确统计证伪/证实。"""
    p1, p3 = env["kp"]["P1"], env["kp"]["P3"]
    s = env["students"]["T01"]
    graph = KpGraph(session, env["kb"].id)
    when = datetime(2025, 11, 1, 12, 0)

    # P1 诊断题高分 -> 掌握度达标 -> 证伪
    for i in range(3):
        tpl = make_exam(session, env["class"].id, f"诊断{i}", date(2025, 10, 1 + i * 10),
                        "诊断", [(1, 10.0, "解答", "应用", [(p1, 1.0)])])
        resp = ExamResponse(exam_template_id=tpl.id, student_id=s, source="excel", status="待审核")
        session.add(resp)
        session.flush()
        session.add(ResponseAnswer(exam_response_id=resp.id, template_question_id=tpl.questions[0].id, score=9.0))
        resp.total_score = 9.0
        commit_exam(session, tpl.id)

    att = _att(s, p3, None, "active", root_kp_id=p1)
    session.add(att)
    session.flush()

    r = verify_attribution_prediction(session, graph, att.id, when)
    assert r["verdict"] == "falsified"

    stats = attribution_closure(session, env["class"].id)
    print(f"\n[closure-verify] {stats}")
    assert stats["by_verdict"]["falsified"] == 1
    assert stats["diagnostic_verified"] == 1
    assert stats["by_status"]["overridden"] == 1
    assert stats["teacher_overridden"] == 0  # 诊断证伪，非人工否决
