"""候选3：班级质量报告计算层（quality_model.py）。

- N+1 修复后统计与逐题得分率正确（批量取 ResponseAnswer 等价于逐条取）；
- 传入 events_by_sk 时不再内部预取（复用调用方批量扫描）。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.kb.graph import KpGraph
from app.ingestion.commit import add_manual_response, commit_exam
from app.models import ExamResponse, TemplateQuestion
from app.reports.quality_model import compute_quality_model
from tests.conftest import add_progress, make_exam


def test_compute_quality_model_rates_totals_and_stats(session, env):
    kp = env["kp"]["P1"]
    tpl = make_exam(
        session, env["class"].id, "期中质量", date(2025, 11, 2), "期中",
        [(1, 10.0, "解答", "应用", [(kp, 1.0)]),
         (2, 5.0, "选择", "识记", [(kp, 1.0)])],
    )
    add_progress(session, env["class"].id, [kp])
    sids = list(env["students"].values())[:2]
    # 两名学生均按 40% 得分率作答（题1=4/10，题2=2/5）→ P1 掌握度 0.4（< floor 0.6 → weak）
    for sid in sids:
        add_manual_response(session, tpl.id, sid, {1: 4.0, 2: 2.0})
    commit_exam(session, tpl.id)  # 派生证据事件（不经报告生成）
    session.flush()

    graph = KpGraph(session, env["kb"].id)
    model = compute_quality_model(session, graph, env["class"].id, tpl.id)

    assert model.committed == 2
    assert model.pending == len(env["students"]) - 2
    assert model.full_total == 15.0
    assert sorted(model.totals) == [6.0, 6.0]
    # 两道题得分率均为 (0.4 + 0.4) / 2 = 0.4 → 均触发低得分率
    assert all(abs(q["rate"] - 0.4) < 1e-9 for q in model.question_rates)
    assert all(q["low"] for q in model.question_rates)
    # kp_stats 覆盖 P1：两名已提交学生均有依据，且均弱
    assert model.kp_stats[kp]["n"] == 2
    assert model.kp_stats[kp]["weak"] == 2


def test_compute_quality_model_reuses_passed_events(session, env, monkeypatch):
    """传入 events_by_sk 后不得再内部 get_events_batch（复用调用方批量扫描）。"""
    import app.reports.quality_model as qm

    kp = env["kp"]["P1"]
    tpl = make_exam(
        session, env["class"].id, "复用卷", date(2025, 11, 2), "单元",
        [(1, 10.0, "解答", "应用", [(kp, 1.0)])],
    )
    add_progress(session, env["class"].id, [kp])
    sid = list(env["students"].values())[0]
    resp = ExamResponse(exam_template_id=tpl.id, student_id=sid, source="excel",
                        status="已提交", total_score=8.0)
    session.add(resp)
    session.flush()
    from app.models import ResponseAnswer

    qs = session.scalars(select(TemplateQuestion).where(
        TemplateQuestion.exam_template_id == tpl.id)).all()
    session.add(ResponseAnswer(exam_response_id=resp.id,
                               template_question_id=qs[0].id, score=8.0))
    session.flush()

    def boom(*a, **kw):
        raise AssertionError("传入 events_by_sk 时不应再内部预取")

    monkeypatch.setattr(qm, "get_events_batch", boom)
    graph = KpGraph(session, env["kb"].id)
    model = compute_quality_model(session, graph, env["class"].id, tpl.id, events_by_sk={})
    assert model.committed == 1
