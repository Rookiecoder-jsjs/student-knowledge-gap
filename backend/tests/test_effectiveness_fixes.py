"""有效性修复的单元测试（R1 低证据软门 / R2 遗忘阈值 / R4 strict 薄弱模式）。"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.db import Base
from app.kb.graph import KpGraph
from app.models import (
    Class,
    ExamResponse,
    KbVersion,
    KnowledgePoint,
    QuestionKp,
    ResponseAnswer,
    School,
    Student,
    TemplateQuestion,
    ExamTemplate,
    TeachingProgress,
)
from app.ingestion.commit import commit_exam
from app.pipeline.attribution import (
    ATTR_FORGET,
    materialize_attribution_verdicts,
)
from app.pipeline.weakness import assess_student_kps
from tests.conftest import make_exam, add_progress


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    s = S()
    yield s
    s.close()


def _dt(d: date) -> datetime:
    return datetime.combine(d, time(12, 0))


# ===========================================================================
# R4 · strict 薄弱模式消除 P25 结构性误报
# ===========================================================================


def _build_strong_class(session):
    """全班均高于底线（0.7-0.95），构造 P25 结构性误报场景。"""
    kb = KbVersion(subject="数学", textbook_edition="t", version="t")
    session.add(kb)
    session.flush()
    kp = KnowledgePoint(kb_version_id=kb.id, code="K1", name="K1", grade=7, semester=1,
                        chapter="ch", cog_levels_expected=["应用"], difficulty_prior=0.5,
                        mastery_floor=0.6)
    session.add(kp)
    session.flush()
    school = School(name="s")
    session.add(school)
    session.flush()
    clazz = Class(school_id=school.id, name="c", grade=7)
    session.add(clazz)
    session.flush()
    session.add(TeachingProgress(class_id=clazz.id, kp_id=kp.id, taught_at=date(2025, 9, 1)))
    targets = [0.70, 0.74, 0.78, 0.82, 0.86, 0.90, 0.92, 0.95]
    sids = []
    for i, t in enumerate(targets, 1):
        stu = Student(school_id=school.id, class_id=clazz.id, name_or_alias=f"S{i}")
        session.add(stu)
        session.flush()
        sids.append(stu.id)
    for ei, d in enumerate([date(2025, 9, 30), date(2025, 11, 10), date(2026, 1, 15)]):
        tpl = ExamTemplate(class_id=clazz.id, name=f"e{ei}", exam_date=d, type="期中")
        session.add(tpl)
        session.flush()
        tq = TemplateQuestion(exam_template_id=tpl.id, idx=1, stem="q", q_type="解答",
                              full_score=10.0, cog_level="应用")
        session.add(tq)
        session.flush()
        session.add(QuestionKp(template_question_id=tq.id, kp_id=kp.id, weight=1.0))
        for sid, t in zip(sids, targets):
            resp = ExamResponse(exam_template_id=tpl.id, student_id=sid, source="excel", status="待审核")
            session.add(resp)
            session.flush()
            sc = round(t * 10 * 2) / 2
            session.add(ResponseAnswer(exam_response_id=resp.id, template_question_id=tq.id, score=sc))
            resp.total_score = sc
    session.flush()
    for tpl in session.scalars(select(ExamTemplate)):
        commit_exam(session, tpl.id)
    return kb, clazz, kp, sids


def test_strict_mode_cuts_p25_fp(session, monkeypatch):
    """standard 模式有 ~25% P25 误报；strict 模式降为 0。"""
    kb, clazz, kp, sids = _build_strong_class(session)
    graph = KpGraph(session, kb.id)
    as_of = _dt(date(2026, 1, 16))

    def count_p25_fp():
        n = 0
        for sid in sids:
            for a in assess_student_kps(session, graph, sid, clazz.id, as_of):
                if a.gate is None and a.is_weak and a.weak_criterion == "班级P25":
                    n += 1
        return n

    monkeypatch.setattr("app.pipeline.weakness.WEAKNESS_MODE", "standard")
    std_fp = count_p25_fp()
    monkeypatch.setattr("app.pipeline.weakness.WEAKNESS_MODE", "strict")
    strict_fp = count_p25_fp()
    print(f"\n[R4] 全班达标：standard P25误报={std_fp}  strict P25误报={strict_fp}")
    assert std_fp > 0, "standard 模式应有 P25 结构性误报"
    assert strict_fp == 0, "strict 模式应消除 P25 结构性误报"


# ===========================================================================
# R1 · 低证据软门：MIN=2 时 2 证据可评估但标记 low_evidence
# ===========================================================================


def test_low_evidence_flag(session, env, monkeypatch):
    """MIN=2：2 证据 kp 可评估且 low_evidence=True；3 证据 low_evidence=False。"""
    p2 = env["kp"]["P2"]
    sids = env["students"]
    add_progress(session, env["class"].id, list(env["kp"].values()))

    # T01：P2 仅 2 场考试（2 证据）；T02：P2 三场考试（3 证据）
    exams = [
        (date(2025, 9, 30), "单元"),
        (date(2025, 11, 10), "期中"),
        (date(2026, 1, 15), "期末"),
    ]
    for i, (d, t) in enumerate(exams):
        tpl = make_exam(session, env["class"].id, f"e{i}", d, t,
                        [(1, 10.0, "解答", "应用", [(p2, 1.0)])])
        # T01 只在前两场作答
        if i < 2:
            _answer_single(session, tpl, sids["T01"], 8.0)
        _answer_single(session, tpl, sids["T02"], 8.0)
        commit_exam(session, tpl.id)

    monkeypatch.setattr("app.pipeline.weakness.MIN_EVIDENCE_COUNT", 2)
    monkeypatch.setattr("app.pipeline.weakness.EVIDENCE_LOW_WATERMARK", 3)
    graph = KpGraph(session, env["kb"].id)
    as_of = _dt(date(2026, 1, 16))

    a_t01 = next(a for a in assess_student_kps(session, graph, sids["T01"], env["class"].id, as_of)
                 if a.kp_code == "P2")
    a_t02 = next(a for a in assess_student_kps(session, graph, sids["T02"], env["class"].id, as_of)
                 if a.kp_code == "P2")
    print(f"\n[R1] T01(2证据): gate={a_t01.gate} low_evidence={a_t01.low_evidence} mastery={a_t01.mastery}")
    print(f"[R1] T02(3证据): gate={a_t02.gate} low_evidence={a_t02.low_evidence} mastery={a_t02.mastery}")
    assert a_t01.gate is None, "MIN=2 时 2 证据应可评估（非数据不足）"
    assert a_t01.low_evidence is True, "2 证据应标记 low_evidence"
    assert a_t02.low_evidence is False, "3 证据不应标记 low_evidence"


def test_min3_gates_2_evidence(session, env, monkeypatch):
    """MIN=3（回退配置）：2 证据 kp 判「数据不足」；生产默认 MIN=2 则可评估。"""
    monkeypatch.setattr("app.pipeline.weakness.MIN_EVIDENCE_COUNT", 3)
    p2 = env["kp"]["P2"]
    sids = env["students"]
    add_progress(session, env["class"].id, list(env["kp"].values()))
    for i, d in enumerate([date(2025, 9, 30), date(2025, 11, 10)]):
        tpl = make_exam(session, env["class"].id, f"e{i}", d, "期中",
                        [(1, 10.0, "解答", "应用", [(p2, 1.0)])])
        _answer_single(session, tpl, sids["T01"], 8.0)
        commit_exam(session, tpl.id)
    graph = KpGraph(session, env["kb"].id)
    a = next(a for a in assess_student_kps(session, graph, sids["T01"], env["class"].id,
                                           _dt(date(2025, 11, 16)))
             if a.kp_code == "P2")
    assert a.gate == "数据不足", "MIN=3 时 2 证据应判数据不足"


def _answer_single(session, tpl, sid, score):
    from app.models import ResponseAnswer
    resp = ExamResponse(exam_template_id=tpl.id, student_id=sid, source="excel", status="待审核")
    session.add(resp)
    session.flush()
    session.add(ResponseAnswer(exam_response_id=resp.id, template_question_id=tpl.questions[0].id, score=score))
    resp.total_score = score
    session.flush()


# ===========================================================================
# R2 · 遗忘阈值降噪：PEAK=0.7 比 0.75 更稳（边界峰值 0.70 可触发）
# ===========================================================================


def test_forget_threshold_robustness(session, env, monkeypatch):
    """峰值 0.70 的遗忘轨迹：PEAK=0.75 不触发，PEAK=0.7 触发。"""
    p3, u = env["kp"]["P3"], env["kp"]["U"]
    sids = env["students"]
    add_progress(session, env["class"].id, list(env["kp"].values()))
    # 三场考试，每卷 P3 + U 两题：T01 的 P3 高(0.70)/高/低(0.30)，U 始终 8（避免总分骤降触发异常降权）
    specs = [
        (date(2025, 9, 30), 7.0),
        (date(2025, 10, 30), 7.0),
        (date(2025, 12, 15), 3.0),
    ]
    for i, (d, p3_sc) in enumerate(specs):
        tpl = make_exam(session, env["class"].id, f"e{i}", d, "期中",
                        [(1, 10.0, "解答", "应用", [(p3, 1.0)]),
                         (2, 10.0, "解答", "应用", [(u, 1.0)])])
        _answer_two(session, tpl, sids["T01"], p3_sc, 8.0)
        for alias in ["T02", "T03", "T04", "T05", "T06"]:
            _answer_two(session, tpl, sids[alias], 8.0, 8.0)
        commit_exam(session, tpl.id)

    graph = KpGraph(session, env["kb"].id)
    as_of = _dt(date(2025, 12, 16))

    def forget_fired():
        active = materialize_attribution_verdicts(session, graph, sids["T01"], env["class"].id, as_of)
        return any(a.type == ATTR_FORGET and a.kp_id == p3 for a in active)

    monkeypatch.setattr("app.pipeline.attribution.FORGET_PEAK_THRESHOLD", 0.75)
    fired_075 = forget_fired()
    monkeypatch.setattr("app.pipeline.attribution.FORGET_PEAK_THRESHOLD", 0.7)
    fired_07 = forget_fired()
    print(f"\n[R2] 峰值0.70轨迹：PEAK=0.75 触发={fired_075}  PEAK=0.7 触发={fired_07}")
    assert not fired_075, "PEAK=0.75 时峰值 0.70 不应触发遗忘"
    assert fired_07, "PEAK=0.7 时峰值 0.70 应触发遗忘（降噪敏感）"


def _answer_two(session, tpl, sid, q1_score, q2_score):
    resp = ExamResponse(exam_template_id=tpl.id, student_id=sid, source="excel", status="待审核")
    session.add(resp)
    session.flush()
    qs = sorted(tpl.questions, key=lambda q: q.idx)
    session.add(ResponseAnswer(exam_response_id=resp.id, template_question_id=qs[0].id, score=q1_score))
    session.add(ResponseAnswer(exam_response_id=resp.id, template_question_id=qs[1].id, score=q2_score))
    resp.total_score = round(q1_score + q2_score, 2)
    session.flush()
