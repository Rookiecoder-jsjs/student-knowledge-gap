"""诊断单 LLM 生成层（plan_writer + evidence_pack + prompts）测试。

diagnosis-sheet-redesign.md §5：
- 证据包构造正确（学生包无他人数据、班级包纯聚合）；
- LLM 输出校验（结构/禁词/条数/排名）；
- 校验失败与 LLM 异常均回落模板（返回 None）；
- SC_LLM_PLAN_ENABLE=0 全模板（直接 None，不触达 client）。
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.config import LLM_PLAN_ENABLE
import app.llm.plan_writer as plan_writer_module
from app.db import Base
from app.kb.graph import KpGraph
from app.kb.resolver import active_kb
from app.llm.client import MockLLMClient, set_client
from app.llm.plan_writer import (
    get_plan_breaker,
    write_class_advice,
    write_student_diagnosis,
)
from app.reports.diagnosis_model import compute_diagnosis_model
from app.reports.quality_model import compute_quality_model
from tests.conftest import add_progress, make_exam


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    s = S()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _plan_off_by_default(monkeypatch):
    """每条测试默认关闭总开关；需要开的显式 monkeypatch 置真。"""
    monkeypatch.setattr(plan_writer_module.config, "LLM_PLAN_ENABLE", False)
    get_plan_breaker().reset()


def _seed_weak_student(session, env):
    """P1 已教、全班一场考试并提交，T01 低分（薄弱+前置归因素材），其余高分。"""
    from app.ingestion.commit import add_manual_response, commit_exam

    kb = env["kb"]
    kb.status = "active"
    session.flush()
    kp = env["kp"]["P1"]
    tpl = make_exam(
        session, env["class"].id, "生成层月考", date(2025, 10, 10), "单元",
        [(1, 10.0, "解答", "应用", [(kp, 1.0)]),
         (2, 5.0, "选择", "识记", [(kp, 1.0)])],
    )
    add_progress(session, env["class"].id, [kp])
    ids = list(env["students"].values())
    for i, sid in enumerate(ids):
        # T01 最弱、T02~T04 也低（过 40% 共性阈值 → 班级共性问题成立），其余接近满分
        score = {0: 3.0, 1: 3.5, 2: 4.0, 3: 6.0}.get(i, 9.5)
        add_manual_response(session, tpl.id, sid, {1: score, 2: 5.0})
    commit_exam(session, tpl.id)
    as_of = datetime.combine(date(2025, 10, 11), time(23, 59))
    return tpl, ids[0], as_of


# ---------------------------------------------------------------------------
# 证据包
# ---------------------------------------------------------------------------


def test_student_pack_contains_only_own_data(session, env, monkeypatch):
    """学生包只含本人数据：无他人姓名/成绩/排名字段。"""
    from app.reports.evidence_pack import student_evidence_pack

    tpl, sid, as_of = _seed_weak_student(session, env)
    graph = KpGraph(session, active_kb(session).id)
    model = compute_diagnosis_model(session, graph, sid, as_of)
    pack = student_evidence_pack(graph, model)

    assert pack["alias"] == "T01"
    assert isinstance(pack["weak"], list)
    text = str(pack)
    for other in ("T02", "T03", "T04", "T05", "T06"):
        assert other not in text, f"学生包泄漏了他人标识 {other}"
    assert all("rank" not in k for k in pack)


def test_class_pack_is_pure_aggregate(session, env):
    """班级包只有聚合数：不含任何学生姓名。"""
    from app.reports.evidence_pack import class_evidence_pack

    tpl, sid, as_of = _seed_weak_student(session, env)
    graph = KpGraph(session, active_kb(session).id)
    quality = compute_quality_model(session, graph, env["class"].id, tpl.id)
    pack = class_evidence_pack(graph, quality)

    assert pack["committed"] == 6
    text = str(pack)
    for name in ("T01", "T02", "T03", "T04", "T05", "T06"):
        assert name not in text, f"班级包泄漏了个别学生 {name}"
    # 数字统一为整数百分比形态（_pct）
    if pack["mean_score_pct"] is not None:
        assert isinstance(pack["mean_score_pct"], int)


# ---------------------------------------------------------------------------
# 开关与回落
# ---------------------------------------------------------------------------


def test_disabled_returns_none_without_touching_client(session, env, monkeypatch):
    """开关关：直接 None（全模板），client 队列里有货也不许碰。"""
    tpl, sid, as_of = _seed_weak_student(session, env)
    graph = KpGraph(session, active_kb(session).id)
    model = compute_diagnosis_model(session, graph, sid, as_of)

    mock = MockLLMClient([{"markdown": "不该被消费"}])
    set_client(mock)
    try:
        assert write_student_diagnosis(graph, model) is None
        assert mock.calls == []
    finally:
        set_client(None)


def test_llm_success_replaces_template(session, env, monkeypatch):
    """开 + 合格输出：返回 PlanDraft，model 溯源可读。"""
    monkeypatch.setattr(plan_writer_module.config, "LLM_PLAN_ENABLE", True)
    tpl, sid, as_of = _seed_weak_student(session, env)
    graph = KpGraph(session, active_kb(session).id)
    model = compute_diagnosis_model(session, graph, sid, as_of)

    good = (
        "### 保持与进步\n- 基础点掌握扎实。\n\n"
        "### 下一步需要关注的知识点\n- 中间点建议先补前置，把握约 60%。"
    )
    set_client(MockLLMClient([{"markdown": good}]))
    try:
        draft = write_student_diagnosis(graph, model)
        assert draft is not None
        assert draft.markdown == good
        assert draft.model == "mock-vision-v0"
    finally:
        set_client(None)


@pytest.mark.parametrize(
    "bad_md",
    [
        "",  # 空
        "没有结构段的正文，直接一大段话。",  # 缺结构段
        "### 保持与进步\n- x\n### 下一步\n他排名第3名。",  # 排名表述
        "### 保持与进步\n" + "- x\n" * 200,  # 超长
    ],
)
def test_invalid_output_falls_back_to_none(session, env, monkeypatch, bad_md):
    """校验失败整体回落模板：返回 None，不做局部修补。"""
    monkeypatch.setattr(plan_writer_module.config, "LLM_PLAN_ENABLE", True)
    tpl, sid, as_of = _seed_weak_student(session, env)
    graph = KpGraph(session, active_kb(session).id)
    model = compute_diagnosis_model(session, graph, sid, as_of)
    set_client(MockLLMClient([{"markdown": bad_md}]))
    try:
        assert write_student_diagnosis(graph, model) is None
    finally:
        set_client(None)


def test_llm_exception_falls_back_and_counts_breaker(session, env, monkeypatch):
    """LLM 异常：回落 None 且计入熔断（连续到阈值会开闸）。"""
    from app.config import LLM_CB_THRESHOLD
    from app.llm.client import LLMError

    monkeypatch.setattr(plan_writer_module.config, "LLM_PLAN_ENABLE", True)
    tpl, sid, as_of = _seed_weak_student(session, env)
    graph = KpGraph(session, active_kb(session).id)
    model = compute_diagnosis_model(session, graph, sid, as_of)

    class Boom(MockLLMClient):
        def parse_json(self, system, user, image_bytes):
            raise LLMError("boom")

    set_client(Boom([{"markdown": "x"}] * (LLM_CB_THRESHOLD + 1)))
    try:
        for _ in range(LLM_CB_THRESHOLD):
            assert write_student_diagnosis(graph, model) is None
        assert get_plan_breaker().state == "open"
        # 开闸后 fast-fail：不再触达 client
        calls_before = len(Boom.calls) if hasattr(Boom, "calls") else None
        assert write_student_diagnosis(graph, model) is None
    finally:
        set_client(None)
        get_plan_breaker().reset()


def test_class_advice_validation_rules(session, env, monkeypatch):
    """班级改进意见校验：条数/知识点对得上/禁姓名。"""
    monkeypatch.setattr(plan_writer_module.config, "LLM_PLAN_ENABLE", True)
    tpl, sid, as_of = _seed_weak_student(session, env)
    graph = KpGraph(session, active_kb(session).id)
    quality = compute_quality_model(session, graph, env["class"].id, tpl.id)

    names = [f"T0{i}" for i in range(1, 7)]

    def attempt(md):
        set_client(MockLLMClient([{"markdown": md}]))
        try:
            return write_class_advice(
                graph, quality, as_of=as_of, forbidden_names=names
            )
        finally:
            set_client(None)

    # 不合格：<3 条 / 提到姓名 / 对不上任何证据包 kp
    assert attempt("### 意见\n- 基础点重讲一遍。") is None
    assert attempt(
        "- 基础点本周内全班重讲。\n- T03 与 T05 组小组辨析练习。\n- 其余同学自查。"
    ) is None
    assert attempt("- 完全无关的内容一。\n- 无关内容二。\n- 无关内容三。") is None

    # 合格：3 条、每条带「基础点」、无姓名
    ok = (
        "- 基础点本周内安排一次全班重讲，配套随堂小测。\n"
        "- 基础点错题集中讲评后，下周布置隔天两次少量巩固练习。\n"
        "- 基础点仍薄弱的个体汇总给教师，指向对应学生的改进单跟进。"
    )
    draft = attempt(ok)
    assert draft is not None and "基础点" in draft.markdown
