"""薄弱判定：护栏顺序、双基准、轨迹分类（DESIGN §6）。"""

from __future__ import annotations

from datetime import date, datetime, time

from app.ingestion.commit import commit_exam
from app.models import ExamResponse, ResponseAnswer
from app.pipeline.weakness import (
    GATE_INSUFFICIENT,
    GATE_NOT_LEARNED,
    TRAJ_DECLINING,
    TRAJ_RISING,
    assess_student_kps,
    classify_trajectory,
    percentile,
)
from app.kb.graph import KpGraph
from tests.conftest import add_progress, make_exam


def _bulk_answer(session, tpl, student_id, per_q):
    resp = ExamResponse(exam_template_id=tpl.id, student_id=student_id,
                        source="excel", status="待审核")
    session.add(resp)
    session.flush()
    for q in tpl.questions:
        session.add(ResponseAnswer(exam_response_id=resp.id,
                                   template_question_id=q.id,
                                   score=per_q.get(q.idx, 0.0)))
    resp.total_score = sum(per_q.values())
    session.flush()
    return resp


def _commit_three_exams(session, env, kp_id, scores_by_exam):
    """同一知识点三场考试（过证据门槛），scores_by_exam: [满分10 的得分]"""
    tpls = []
    for i, score in enumerate(scores_by_exam):
        tpl = make_exam(session, env["class"].id, f"E{i}",
                        date(2025, 10, 1 + i * 10), "单元",
                        [(1, 10.0, "解答", "应用", [(kp_id, 1.0)])])
        _bulk_answer(session, tpl, env["students"]["T01"], {1: score})
        commit_exam(session, tpl.id)
        tpls.append(tpl)
    return tpls


def test_not_learned_gate(session, env):
    """教学进度未覆盖 → 未学到，绝不判薄弱。"""
    graph = KpGraph(session, env["kb"].id)
    add_progress(session, env["class"].id, [env["kp"]["P1"]])  # 只教了 P1
    when = datetime(2025, 12, 1, 12, 0)
    res = {a.kp_code: a for a in
           assess_student_kps(session, graph, env["students"]["T01"], env["class"].id, when)}
    assert res["P2"].gate == GATE_NOT_LEARNED
    assert not res["P2"].is_weak


def test_insufficient_gate(session, env):
    kp = env["kp"]["P1"]
    add_progress(session, env["class"].id, [kp])
    tpl = make_exam(session, env["class"].id, "E0", date(2025, 10, 1), "单元",
                    [(1, 10.0, "解答", "应用", [(kp, 1.0)])])
    _bulk_answer(session, tpl, env["students"]["T01"], {1: 0.0})
    commit_exam(session, tpl.id)
    graph = KpGraph(session, env["kb"].id)
    res = {a.kp_code: a for a in
           assess_student_kps(session, graph, env["students"]["T01"], env["class"].id,
                              datetime(2025, 10, 2, 12, 0))}
    assert res["P1"].gate == GATE_INSUFFICIENT  # 仅 1 条证据
    assert not res["P1"].is_weak


def test_floor_criterion(session, env):
    kp = env["kp"]["P1"]
    add_progress(session, env["class"].id, [kp])
    _commit_three_exams(session, env, kp, [3.0, 3.0, 3.0])  # 掌握度≈0.3 < 0.6
    graph = KpGraph(session, env["kb"].id)
    res = {a.kp_code: a for a in
           assess_student_kps(session, graph, env["students"]["T01"], env["class"].id,
                              datetime(2025, 10, 22, 12, 0))}
    assert res["P1"].is_weak
    assert res["P1"].weak_criterion in ("绝对底线", "两者")


def test_stale_flag(session, env):
    """最近证据 > 90 天 → 可能已变化。"""
    kp = env["kp"]["P1"]
    add_progress(session, env["class"].id, [kp])
    _commit_three_exams(session, env, kp, [8.0, 8.0, 8.0])
    graph = KpGraph(session, env["kb"].id)
    res = {a.kp_code: a for a in
           assess_student_kps(session, graph, env["students"]["T01"], env["class"].id,
                              datetime(2026, 3, 1, 12, 0))}  # 距最后一次 130+ 天
    assert res["P1"].stale is True


def test_percentile():
    import pytest

    assert percentile([0.2, 0.4, 0.6, 0.8], 25) == pytest.approx(0.35)
    assert percentile([0.5], 25) == 0.5


def test_trajectory_classification(session, env):
    kp = env["kp"]["P1"]
    add_progress(session, env["class"].id, [kp])
    _commit_three_exams(session, env, kp, [2.0, 5.0, 9.0])  # 明显上升
    graph = KpGraph(session, env["kb"].id)
    res = {a.kp_code: a for a in
           assess_student_kps(session, graph, env["students"]["T01"], env["class"].id,
                              datetime(2025, 10, 22, 12, 0))}
    assert res["P1"].trajectory == TRAJ_RISING
