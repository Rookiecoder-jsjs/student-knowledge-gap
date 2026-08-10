"""候选1：归因解析深模块（resolve_attributions）derive-on-read 测试。

证伪「必须先 run_attribution 打底才能生成诊断」的隐含前置条件（不变量②）：
- 零 Attribution 行也能解析出推导归因；
- overridden 裁决（教师否决/诊断题证伪）叠加并跨重跑保留；
- resolved 历史行不复活；
- 传入 assessments 复用，不再二次 assess；
- 全局薄弱抑制逻辑迁移后仍生效；
- 集成：不物化直接 generate_student_diagnosis → 归因段正确渲染。
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.db import Base
from app.ingestion.commit import commit_exam
from app.kb.graph import KpGraph
from app.models import (
    Attribution,
    Class,
    ExamResponse,
    ExamTemplate,
    KbVersion,
    KnowledgePoint,
    KpRelation,
    QuestionKp,
    ResponseAnswer,
    School,
    Student,
    TeachingProgress,
    TemplateQuestion,
)
from app.pipeline.attribution import (
    ATTR_PREREQ,
    GLOBAL_WEAK_CONF_CAP,
    resolve_attributions,
)
from app.pipeline.weakness import assess_student_kps
from app.reports.student_diagnosis import generate_student_diagnosis


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

    for kid in kp_ids.values():
        session.add(TeachingProgress(class_id=clazz.id, kp_id=kid, taught_at=date(2025, 9, 1)))
    session.commit()
    return {"kb": kb, "kp": kp_ids, "class": clazz, "students": students}


def _make_exam(session, class_id, name, exam_date, type_, kp_ids, scores_by_student):
    """每 kp 一道解答题（full 10）；scores_by_student: {student_id: {kp_code: score}}."""
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
    """3 场考试：T01 全局弱(0.4)、T02-T06 正常(0.8)、T07 仅 P1/P2 弱。

    commit 不再生成报告（候选4）：本组测试聚焦归因解析，造数阶段不产生
    Attribution 行，以证伪「必须先打底」（零行也能解析出推导归因）。
    """
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


def test_resolve_derives_fresh_without_priming(session, env):
    """零 Attribution 行 → resolve 仍返回推导归因（证伪「必须先打底」）。"""
    _build_class_data(session, env)
    graph = KpGraph(session, env["kb"].id)
    as_of = _dt(date(2026, 1, 16))
    sid = env["students"]["T07"]

    assert session.scalars(select(Attribution)).all() == [], "前置：无任何 Attribution 行"
    resolved = resolve_attributions(session, graph, sid, env["class"].id, as_of)
    assert resolved, "无打底行也应收敛出推导归因"
    p2 = [r for r in resolved if r.kp_id == env["kp"]["P2"] and r.type == ATTR_PREREQ]
    assert p2 and p2[0].verdict == "active", "P2 前置缺陷应推导为 active（无裁决叠加）"
    assert p2[0].root_kp_id == env["kp"]["P1"], "前置缺陷根因应为 P1"


def test_resolve_overlays_overridden_verdict(session, env):
    """预置 overridden 行 → 对应假设 verdict=overridden 且带 teacher_note。"""
    from app.pipeline.attribution import materialize_attribution_verdicts

    _build_class_data(session, env)
    graph = KpGraph(session, env["kb"].id)
    as_of = _dt(date(2026, 1, 16))
    sid = env["students"]["T07"]

    # 先物化产生 active 行，再教师否决 P2 前置缺陷
    materialize_attribution_verdicts(session, graph, sid, env["class"].id, as_of)
    p2 = session.scalar(
        select(Attribution).where(
            Attribution.student_id == sid,
            Attribution.kp_id == env["kp"]["P2"],
            Attribution.type == ATTR_PREREQ,
        )
    )
    assert p2 is not None
    p2.status = "overridden"
    p2.teacher_note = "教师否决：该生其实掌握了前置知识，是粗心"
    session.flush()

    resolved = resolve_attributions(session, graph, sid, env["class"].id, as_of)
    r = next(r for r in resolved if r.kp_id == env["kp"]["P2"] and r.type == ATTR_PREREQ)
    assert r.verdict == "overridden"
    assert r.teacher_note == "教师否决：该生其实掌握了前置知识，是粗心"
    assert r.prediction, "被否决时仍保留系统推导内容，供人工比对"


def test_resolve_ignores_resolved_rows(session, env):
    """resolved 行不参与叠加（不复活已失效假设）。"""
    from app.pipeline.attribution import materialize_attribution_verdicts

    _build_class_data(session, env)
    graph = KpGraph(session, env["kb"].id)
    as_of = _dt(date(2026, 1, 16))
    sid = env["students"]["T07"]

    materialize_attribution_verdicts(session, graph, sid, env["class"].id, as_of)
    p2 = session.scalar(
        select(Attribution).where(
            Attribution.student_id == sid,
            Attribution.kp_id == env["kp"]["P2"],
            Attribution.type == ATTR_PREREQ,
        )
    )
    p2.status = "resolved"
    session.flush()

    resolved = resolve_attributions(session, graph, sid, env["class"].id, as_of)
    r = next(r for r in resolved if r.kp_id == env["kp"]["P2"] and r.type == ATTR_PREREQ)
    assert r.verdict == "active", "resolved 历史行不复活为 overridden"
    assert r.teacher_note is None


def test_resolve_reuses_passed_assessments(session, env, monkeypatch):
    """传入 assessments → 不再二次 assess（计数 0）；缺省 → 内部 assess 一次。"""
    _build_class_data(session, env)
    graph = KpGraph(session, env["kb"].id)
    as_of = _dt(date(2026, 1, 16))
    sid = env["students"]["T07"]

    real = assess_student_kps
    calls = {"n": 0}

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr("app.pipeline.attribution.assess_student_kps", counting)

    assessments = real(session, graph, sid, env["class"].id, as_of)
    assert calls["n"] == 0, "预取 assessments 不经过 attribution 模块"
    resolve_attributions(session, graph, sid, env["class"].id, as_of, assessments=assessments)
    assert calls["n"] == 0, "传入 assessments 后 resolve 不得再 assess"
    resolve_attributions(session, graph, sid, env["class"].id, as_of)
    assert calls["n"] == 1, "缺省时 resolve 内部 assess 恰好一次"


def test_resolve_global_weak_suppression(session, env):
    """全局薄弱 → 前置缺陷 confidence 封顶（抑制逻辑迁移后仍生效）。"""
    _build_class_data(session, env)
    graph = KpGraph(session, env["kb"].id)
    as_of = _dt(date(2026, 1, 16))
    sid = env["students"]["T01"]

    resolved = resolve_attributions(session, graph, sid, env["class"].id, as_of)
    prereq = [r for r in resolved if r.type == ATTR_PREREQ]
    assert prereq, "全局薄弱学生应产生前置缺陷归因"
    for r in prereq:
        assert r.confidence <= GLOBAL_WEAK_CONF_CAP, (
            f"全局薄弱学生的前置归因置信度 {r.confidence} 未被压到 cap {GLOBAL_WEAK_CONF_CAP}"
        )
        assert any(e.get("global_weak") for e in r.evidence), "evidence 缺 global_weak 标注"


def test_diagnosis_no_longer_silent_without_priming(session, env):
    """集成：不物化直接 generate_student_diagnosis → 归因段正确渲染。"""
    _build_class_data(session, env)
    graph = KpGraph(session, env["kb"].id)
    as_of = _dt(date(2026, 1, 16))
    sid = env["students"]["T07"]

    assert session.scalars(select(Attribution)).all() == [], "前置：无任何 Attribution 行"
    report = generate_student_diagnosis(session, graph, sid, as_of)
    assert "基础没打牢" in report.content_markdown, (
        "推导归因应渲染口语标签（P2 前置缺陷），而非静默落到「暂未匹配」"
    )
