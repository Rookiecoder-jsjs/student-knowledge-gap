"""知识点进度生命周期折叠测试（progress-loop-design P1）。

覆盖：十态全枚举（门槛两态 / 达标 / 薄弱待干预 / 已建议 / 已跳过 / 待复测 /
已闭合 / 持平复评 / 未闭合）+ 班级 scope 行映射 + 多轮 done 取最新 +
阈值判定与 intervention_effect 同口径。证据走真实管线
（ExamResponse → commit_exam → EvidenceEvent），与生产同口径
（MIN_EVIDENCE_COUNT=2：每 kp 2 题才可评估）。

阈值边界（INTERVENTION_MIN_DELTA=0.10 / FLAT_FLOOR=-0.05）：
- 复测 9.0/10：post≈0.606，delta≈+0.31 → 已闭合；
- 复测 3.5/10：post≈0.326，delta≈+0.026 → 持平复评（区间中段，留足浮点余量）；
- 复测 1.0/10：post≈0.198，delta≈-0.10 → 未闭合。
班级基线调整窗口内同伴无新证据 → class_delta=0，adjusted=raw。
"""

from __future__ import annotations

from datetime import date, datetime, time

from app.intervention import (
    KIND_RETEACH,
    SCOPE_CLASS,
    SCOPE_STUDENT,
    intervention_effect,
)
from app.kb.graph import KpGraph
from app.models import (
    ExamResponse,
    ExamTemplate,
    Intervention,
    ResponseAnswer,
    StudyRecord,
)
from app.pipeline.progress import (
    LOOP_AWAITING,
    LOOP_CLOSED,
    LOOP_FLAT,
    LOOP_INSUFFICIENT,
    LOOP_NOT_LEARNED,
    LOOP_ON_TRACK,
    LOOP_OPEN,
    LOOP_SELF_REPORTED,
    LOOP_SKIPPED,
    LOOP_SUGGESTED,
    LOOP_WEAK_NEW,
    loop_states_for_student,
    row_loop_state,
)
from tests.conftest import add_progress, dt, make_exam
from tests.test_intervention_model import _commit, _run


def _weak_u(session, env, *, exam_date=date(2025, 10, 10), n_q=2):
    """U 点 n_q 题（≥MIN_EVIDENCE_COUNT），T01 弱（3/10）其余好（9/10）。"""
    kp_id = env["kp"]["U"]
    add_progress(session, env["class"].id, [kp_id])
    tags = [(kp_id, 1.0)] * n_q
    tpl = make_exam(
        session, env["class"].id, "U测", exam_date, "单元",
        [(i + 1, 10.0, "解答", "应用", [tags[i]]) for i in range(n_q)],
    )
    _run(session, env, tpl, {"T01"})
    return tpl


def _row(session, env, tpl, *, student_id=None, status="done",
         done_at=dt(date(2025, 10, 20)), baseline=None, kind="spaced_review",
         suggested_at=None):
    """手工事实行（与 _make_done_row 同纪律：直接落库，绕过生成器）。

    suggested_at 显式给定（轮次锚点：自报须晚于建议行创建，生产由 utcnow
    默认值承担——测试里隐式真值会破坏确定性）。
    """
    row = Intervention(
        class_id=env["class"].id, student_id=student_id, kp_id=env["kp"]["U"],
        exam_id=tpl.id, kind=kind,
        scope=SCOPE_STUDENT if student_id is not None else SCOPE_CLASS,
        baseline_as_of=baseline or dt(date(2025, 10, 10)), status=status,
        suggested_at=suggested_at or dt(date(2025, 10, 15)),
        done_at=done_at if status == "done" else None,
    )
    session.add(row)
    session.flush()
    return row


def _retest(session, env, d, score, *, n_q=2):
    """诊断复测卷：仅 T01 作答（score/题），提交派生证据（occurred_at=d 正午）。"""
    kp_id = env["kp"]["U"]
    tags = [(kp_id, 1.0)] * n_q
    tpl = make_exam(
        session, env["class"].id, "复测小卷", d, "诊断",
        [(i + 1, 10.0, "解答", "应用", [tags[i]]) for i in range(n_q)],
    )
    resp = ExamResponse(exam_template_id=tpl.id, student_id=env["students"]["T01"],
                        source="manual", status="待审核")
    session.add(resp)
    session.flush()
    for q in tpl.questions:
        session.add(ResponseAnswer(exam_response_id=resp.id,
                                   template_question_id=q.id, score=score))
    resp.total_score = score * len(tpl.questions)
    session.flush()
    _commit(session, tpl)
    return tpl


def _fold(session, env, alias, as_of):
    graph = KpGraph(session, env["kb"].id)
    return loop_states_for_student(
        session, graph, env["students"][alias], env["class"].id, as_of
    )


def _latest_tpl(session):
    return session.query(ExamTemplate).order_by(ExamTemplate.id.desc()).first()


def _state(session, env, alias, as_of, kp="U"):
    return _fold(session, env, alias, as_of)[env["kp"][kp]]


# ---------------------------------------------------------------------------
# 门槛两态 + 达标 / 薄弱待干预
# ---------------------------------------------------------------------------


def test_gate_states_not_learned_and_insufficient(session, env):
    """未教 → 未学到；教了无证据 → 证据不足；永不判薄弱。"""
    add_progress(session, env["class"].id, [env["kp"]["U"]])
    out = _fold(session, env, "T01", dt(date(2025, 11, 1)))
    assert out[env["kp"]["P1"]] == LOOP_NOT_LEARNED
    assert out[env["kp"]["U"]] == LOOP_INSUFFICIENT


def test_on_track_and_weak_new(session, env):
    """达标（非薄弱）/ 薄弱无干预行 → 薄弱待干预。"""
    _weak_u(session, env)
    as_of = dt(date(2025, 11, 1))
    assert _state(session, env, "T01", as_of) == LOOP_WEAK_NEW
    assert _state(session, env, "T02", as_of) == LOOP_ON_TRACK


# ---------------------------------------------------------------------------
# 建议与跳过
# ---------------------------------------------------------------------------


def test_suggested(session, env):
    _weak_u(session, env)
    tpl = _latest_tpl(session)
    _row(session, env, tpl, student_id=env["students"]["T01"], status="suggested")
    assert _state(session, env, "T01", dt(date(2025, 11, 1))) == LOOP_SUGGESTED


def test_skipped(session, env):
    _weak_u(session, env)
    tpl = _latest_tpl(session)
    _row(session, env, tpl, student_id=env["students"]["T01"], status="skipped")
    assert _state(session, env, "T01", dt(date(2025, 11, 1))) == LOOP_SKIPPED


# ---------------------------------------------------------------------------
# 待复测（done 无干预后证据）+ 班级行映射
# ---------------------------------------------------------------------------


def test_awaiting_retest_student_row(session, env):
    _weak_u(session, env)
    tpl = _latest_tpl(session)
    _row(session, env, tpl, student_id=env["students"]["T01"], status="done")
    assert _state(session, env, "T01", dt(date(2025, 11, 1))) == LOOP_AWAITING


def test_class_row_done_maps_to_all_weak_students(session, env):
    """班级 reteach 行（student 空）对该点所有薄弱学生生效；达标学生不受影响。"""
    _weak_u(session, env)
    tpl = _latest_tpl(session)
    _row(session, env, tpl, student_id=None, status="done", kind=KIND_RETEACH)
    as_of = dt(date(2025, 11, 1))
    assert _state(session, env, "T01", as_of) == LOOP_AWAITING
    assert _state(session, env, "T02", as_of) == LOOP_ON_TRACK


# ---------------------------------------------------------------------------
# 效果判决三态（阈值与 intervention_effect 同口径）
# ---------------------------------------------------------------------------


def test_closed_improved(session, env):
    _weak_u(session, env)
    tpl = _latest_tpl(session)
    row = _row(session, env, tpl, student_id=env["students"]["T01"], status="done")
    _retest(session, env, date(2025, 10, 25), 9.0)
    as_of = dt(date(2025, 11, 1))
    assert _state(session, env, "T01", as_of) == LOOP_CLOSED
    # 与 intervention_effect 判决一致（同一阈值常量）
    graph = KpGraph(session, env["kb"].id)
    assert intervention_effect(session, graph, row.id, now=as_of)["effect_status"] == "improved"


def test_flat_re_evaluate(session, env):
    _weak_u(session, env)
    tpl = _latest_tpl(session)
    _row(session, env, tpl, student_id=env["students"]["T01"], status="done")
    _retest(session, env, date(2025, 10, 25), 3.5)
    assert _state(session, env, "T01", dt(date(2025, 11, 1))) == LOOP_FLAT


def test_open_declined(session, env):
    _weak_u(session, env)
    tpl = _latest_tpl(session)
    _row(session, env, tpl, student_id=env["students"]["T01"], status="done")
    _retest(session, env, date(2025, 10, 25), 1.0)
    assert _state(session, env, "T01", dt(date(2025, 11, 1))) == LOOP_OPEN


# ---------------------------------------------------------------------------
# 多轮 done：最新 done_at 决定阶段
# ---------------------------------------------------------------------------


def test_multi_round_latest_done_wins(session, env):
    """二轮干预（新 done 行）落在一轮复测证据之后 → 回到待复测，不误判已闭合。"""
    _weak_u(session, env)
    tpl = _latest_tpl(session)
    _row(session, env, tpl, student_id=env["students"]["T01"], status="done",
         done_at=dt(date(2025, 10, 20)))
    _retest(session, env, date(2025, 10, 25), 9.0)  # 一轮复测有效
    _row(session, env, tpl, student_id=env["students"]["T01"], status="done",
         done_at=dt(date(2025, 10, 30)), baseline=dt(date(2025, 10, 10)))
    assert _state(session, env, "T01", dt(date(2025, 11, 1))) == LOOP_AWAITING


# ---------------------------------------------------------------------------
# 集体行三态视图
# ---------------------------------------------------------------------------


def test_row_loop_state_collective(session, env):
    _weak_u(session, env)
    tpl = _latest_tpl(session)
    assert row_loop_state(_row(session, env, tpl, status="suggested")) == LOOP_SUGGESTED
    assert row_loop_state(_row(session, env, tpl, status="done")) == LOOP_AWAITING
    assert row_loop_state(_row(session, env, tpl, status="skipped")) == LOOP_SKIPPED


# ---------------------------------------------------------------------------
# 自报待检验（study-loop-design 软闭合）：done 无证据时的过渡态
# ---------------------------------------------------------------------------


def _self_report(session, env, alias, d, *, kp="U"):
    """直落一条自报事实（绕过端点；时间语义 = 当天正午 dt）。"""
    rec = StudyRecord(
        class_id=env["class"].id, student_id=env["students"][alias],
        kp_id=env["kp"][kp], intervention_id=None,
        plan_markdown="### 先补这一步\n- 模板", plan_writer={"template": True},
        generated_at=dt(d), self_marked_at=dt(d),
    )
    session.add(rec)
    session.flush()
    return rec


def test_self_reported_replaces_awaiting(session, env):
    """done 行 + 当前轮自报（>= 该轮 baseline）→ 自报待检验；无自报仍待复测。"""
    _weak_u(session, env)
    tpl = _latest_tpl(session)
    _row(session, env, tpl, student_id=env["students"]["T01"], status="done")
    as_of = dt(date(2025, 11, 1))
    assert _state(session, env, "T01", as_of) == LOOP_AWAITING
    _self_report(session, env, "T01", date(2025, 10, 22))
    assert _state(session, env, "T01", as_of) == LOOP_SELF_REPORTED


def test_stale_self_report_keeps_awaiting(session, env):
    """旧轮自报（< 当前轮 baseline_as_of）不伪装新轮进度 → 仍待复测。"""
    _weak_u(session, env)
    tpl = _latest_tpl(session)
    _row(session, env, tpl, student_id=env["students"]["T01"], status="done")
    _self_report(session, env, "T01", date(2025, 10, 5))  # 早于 baseline 10-10
    assert _state(session, env, "T01", dt(date(2025, 11, 1))) == LOOP_AWAITING


def test_self_report_with_post_evidence_yields_verdict(session, env):
    """自报后复测证据到来 → 判决接管（自报待检验只是证据缺席时的过渡态）。"""
    _weak_u(session, env)
    tpl = _latest_tpl(session)
    _row(session, env, tpl, student_id=env["students"]["T01"], status="done")
    _self_report(session, env, "T01", date(2025, 10, 22))
    _retest(session, env, date(2025, 10, 25), 9.0)
    assert _state(session, env, "T01", dt(date(2025, 11, 1))) == LOOP_CLOSED


def test_self_reported_with_pending_suggested_row(session, env):
    """仅有 suggested 行 + 当前轮自报 → 自报待检验（学生自身事实主导个体视图，
    不被仍挂起的班级行掩盖；集体行动走教师队列视图）。"""
    _weak_u(session, env)
    tpl = _latest_tpl(session)
    _row(session, env, tpl, student_id=env["students"]["T01"], status="suggested")
    _self_report(session, env, "T01", date(2025, 10, 22))
    assert _state(session, env, "T01", dt(date(2025, 11, 1))) == LOOP_SELF_REPORTED


def test_self_reported_survives_mixed_done_and_suggested_rows(session, env):
    """done 个体行（自报消化）+ 挂起班级行并存 → 自报待检验（浏览器实测回归）。"""
    _weak_u(session, env)
    tpl = _latest_tpl(session)
    _row(session, env, tpl, student_id=env["students"]["T01"], status="done",
         done_at=dt(date(2025, 10, 22)))
    _row(session, env, tpl, student_id=None, status="suggested", kind=KIND_RETEACH)
    _self_report(session, env, "T01", date(2025, 10, 22))
    assert _state(session, env, "T01", dt(date(2025, 11, 1))) == LOOP_SELF_REPORTED
