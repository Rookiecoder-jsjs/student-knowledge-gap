"""全局薄弱抑制器行为测试（effectiveness-validation-plan V3）。

验证：全局薄弱学生（多数 kp 低于底线）的前置缺陷归因置信度被下调并标注；
targeted-weak 学生（仅少数 kp 弱）不受影响，金标基线语义不变。
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.db import Base
from app.kb.graph import KpGraph
from app.models import (
    Class,
    ExamResponse,
    KbVersion,
    KnowledgePoint,
    KpRelation,
    ResponseAnswer,
    School,
    Student,
    TeachingProgress,
)
from app.pipeline.attribution import (
    ATTR_PREREQ,
    GLOBAL_WEAK_CONF_CAP,
    materialize_attribution_verdicts,
)
from app.ingestion.commit import commit_exam


@pytest.fixture()
def session():
    from sqlalchemy import create_engine

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    s = S()
    yield s
    s.close()


def _dt(d: date) -> datetime:
    return datetime.combine(d, time(12, 0))


@pytest.fixture()
def env(session):
    """6 知识点链 P1->P2->P3->P4->P5 + 独立点 U；7 学生。"""
    kb = KbVersion(subject="数学", textbook_edition="测试版", version="t")
    session.add(kb)
    session.flush()

    codes = ["P1", "P2", "P3", "P4", "P5", "U"]
    kp_ids: dict[str, int] = {}
    for code in codes:
        kp = KnowledgePoint(
            kb_version_id=kb.id, code=code, name=code, grade=7, semester=1,
            chapter="测试章", cog_levels_expected=["应用"], difficulty_prior=0.5,
            mastery_floor=0.6,
        )
        session.add(kp)
        session.flush()
        kp_ids[code] = kp.id

    # 前置链 P1->P2->P3->P4->P5（from 是 to 的前置）
    for a, b in [("P1", "P2"), ("P2", "P3"), ("P3", "P4"), ("P4", "P5")]:
        session.add(KpRelation(from_kp_id=kp_ids[a], to_kp_id=kp_ids[b],
                               type="prerequisite", weight=0.9))

    school = School(name="测试学校")
    session.add(school)
    session.flush()
    clazz = Class(school_id=school.id, name="测试班", grade=7)
    session.add(clazz)
    session.flush()

    students: dict[str, int] = {}
    for i in range(1, 8):
        stu = Student(school_id=school.id, class_id=clazz.id, name_or_alias=f"T{i:02d}")
        session.add(stu)
        session.flush()
        students[f"T{i:02d}"] = stu.id

    # 教学进度覆盖全部 6 个 kp
    for kid in kp_ids.values():
        session.add(TeachingProgress(class_id=clazz.id, kp_id=kid, taught_at=date(2025, 9, 1)))
    session.commit()
    return {"kb": kb, "kp": kp_ids, "class": clazz, "students": students}


def _make_exam(session, class_id, name, exam_date, type_, kp_ids, scores_by_student):
    """建卷：每个 kp 一道解答题（full 10）；scores_by_student: {student_id: {kp_code: score}}."""
    from app.models import ExamTemplate, TemplateQuestion, QuestionKp

    tpl = ExamTemplate(class_id=class_id, name=name, exam_date=exam_date, type=type_)
    session.add(tpl)
    session.flush()
    qs = []
    for idx, code in enumerate(kp_ids, start=1):
        tq = TemplateQuestion(exam_template_id=tpl.id, idx=idx, stem=f"题{idx}",
                              q_type="解答", full_score=10.0, cog_level="应用")
        session.add(tq)
        session.flush()
        session.add(QuestionKp(template_question_id=tq.id, kp_id=kp_ids[code], weight=1.0))
        qs.append((code, tq.id))
    for sid, scores in scores_by_student.items():
        resp = ExamResponse(exam_template_id=tpl.id, student_id=sid, source="excel",
                            status="待审核")
        session.add(resp)
        session.flush()
        total = 0.0
        for code, qid in qs:
            sc = scores.get(code, 0.0)
            total += sc
            session.add(ResponseAnswer(exam_response_id=resp.id,
                                       template_question_id=qid, score=sc))
        resp.total_score = round(total, 2)
    session.flush()
    return tpl


def _build_class_data(session, env):
    """3 场考试：T01 全局弱(0.4)、T02-T06 正常(0.8)、T07 仅 P1/P2 弱。"""
    kp_ids = env["kp"]
    sid = env["students"]
    dates = [(date(2025, 9, 30), "单元"), (date(2025, 11, 10), "期中"), (date(2026, 1, 15), "期末")]

    def scores_for(alias):
        if alias == "T01":
            return {c: 4.0 for c in kp_ids}            # 全局弱 0.4
        if alias == "T07":
            return {**{c: 8.0 for c in kp_ids}, "P1": 4.0, "P2": 4.0}  # 仅 P1/P2 弱
        return {c: 8.0 for c in kp_ids}                # 正常 0.8

    for d, t in dates:
        scores_by_student = {sid[a]: scores_for(a) for a in env["students"]}
        tpl = _make_exam(session, env["class"].id, f"考_{d}", d, t, kp_ids, scores_by_student)
        commit_exam(session, tpl.id)


def test_global_weak_dampened(session, env):
    """全局薄弱学生：前置缺陷归因置信度被压到 cap，evidence 含 global_weak。"""
    _build_class_data(session, env)
    graph = KpGraph(session, env["kb"].id)
    as_of = _dt(date(2026, 1, 16))
    s01 = env["students"]["T01"]

    active = materialize_attribution_verdicts(session, graph, s01, env["class"].id, as_of)
    prereq = [a for a in active if a.type == ATTR_PREREQ]
    assert len(prereq) >= 1, "全局薄弱学生应产生前置缺陷归因"
    print(f"\n[抑制器] T01 前置归因 {len(prereq)} 条，置信度 {[a.confidence for a in prereq]}")
    for a in prereq:
        assert a.confidence <= GLOBAL_WEAK_CONF_CAP, (
            f"全局薄弱学生的前置归因置信度 {a.confidence} 未被压到 cap {GLOBAL_WEAK_CONF_CAP}"
        )
        ev = a.evidence_json or []
        assert any(e.get("global_weak") for e in ev), "evidence 缺 global_weak 标注"


def test_targeted_weak_not_dampened(session, env):
    """targeted-weak 学生（仅 P1/P2 弱，占比 2/6=0.33）：前置归因置信度不被压。"""
    _build_class_data(session, env)
    graph = KpGraph(session, env["kb"].id)
    as_of = _dt(date(2026, 1, 16))
    s07 = env["students"]["T07"]

    active = materialize_attribution_verdicts(session, graph, s07, env["class"].id, as_of)
    prereq = [a for a in active if a.type == ATTR_PREREQ]
    # P2 弱且 P1 低 -> 应有 P2 的前置缺陷归因
    p2_att = next((a for a in prereq if a.kp_id == env["kp"]["P2"]), None)
    assert p2_att is not None, "T07 的 P2 应有前置缺陷归因（P1 低）"
    assert p2_att.confidence > GLOBAL_WEAK_CONF_CAP, (
        f"targeted-weak 学生置信度 {p2_att.confidence} 不应被压到 cap"
    )
    ev = p2_att.evidence_json or []
    assert not any(e.get("global_weak") for e in ev), "targeted-weak 不应触发 global_weak"
    print(f"\n[抑制器] T07(P2) 置信度 {p2_att.confidence}（未抑制，> cap {GLOBAL_WEAK_CONF_CAP}）")
