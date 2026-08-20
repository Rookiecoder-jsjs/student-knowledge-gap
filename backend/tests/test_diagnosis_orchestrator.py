"""diagnosis_orchestrator 三分支单测（架构修复 候选2：不经 HTTP 的领域层测试面）。

覆盖 get-or-generate 决策：指定考试 / 最近一场 / 自定义 as_of，
以及 exam_id 不存在、narrative 缓存、quality get-or-generate。
物化尾步（materialize）与诊断渲染 derive-on-read 的耦合不在此测——已有
test_auto_report / test_attribution_resolve 覆盖。
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.kb.graph import KpGraph
from app.llm.client import MockLLMClient, set_client
from app.llm.gateway import get_text_breaker
from app.llm.prompts import NARRATIVE_PROMPT_VERSION
from app.models import Report
from app.reports.diagnosis_orchestrator import (
    get_or_create_narrative,
    get_or_generate_diagnosis,
    get_or_generate_quality_report,
)
from tests.conftest import make_exam

T01 = "T01"


def _exam(session, env, day=date(2025, 9, 10)):
    return make_exam(
        session,
        env["class"].id,
        "期中",
        day,
        "单元",
        [
            (1, 10, "选择", "应用", [(env["kp"]["P1"], 1.0)]),
        ],
    )


def _graph(session, env):
    return KpGraph(session, env["kb"].id)


def _diagnoses(session) -> list[Report]:
    stmt = (
        select(Report)
        .where(Report.type == "student_diagnosis")
        .order_by(Report.id)
    )
    return list(session.scalars(stmt))


@pytest.fixture()
def graph(env, session):
    return _graph(session, env)


# ---------------------------------------------------------------------------
# 指定考试（exam_id）
# ---------------------------------------------------------------------------


def test_exam_id_generates_when_absent(env, session, graph):
    """exam_id 无已存诊断 -> 按考试日补生成落库，关联 exam_id，物化尾步不抛错。"""
    exam = _exam(session, env)
    session.commit()

    report, generated = get_or_generate_diagnosis(
        session, graph, env["students"][T01], exam_id=exam.id
    )

    assert generated is True
    assert report.type == "student_diagnosis"
    assert report.student_id == env["students"][T01]
    assert report.exam_id == exam.id


def test_exam_id_returns_existing(env, session, graph):
    """exam_id 已有已存诊断 -> 原样返回，不再生成（不新增行）。"""
    exam = _exam(session, env)
    session.commit()
    report, _ = get_or_generate_diagnosis(session, graph, env["students"][T01], exam_id=exam.id)
    session.commit()

    report2, generated = get_or_generate_diagnosis(
        session, graph, env["students"][T01], exam_id=exam.id
    )

    assert generated is False
    assert report2.id == report.id
    assert len(_diagnoses(session)) == 1


def test_exam_id_missing_raises(env, session, graph):
    """exam_id 对应的考试不存在 -> ValueError（路由层翻译 404）。"""
    with pytest.raises(ValueError, match="考试不存在"):
        get_or_generate_diagnosis(session, graph, env["students"][T01], exam_id=9999)


# ---------------------------------------------------------------------------
# 自定义 as_of（现算）
# ---------------------------------------------------------------------------


def test_as_of_computes_fresh(env, session, graph):
    """显式 as_of -> 现算并落库（不查已存），generated=True。"""
    report, generated = get_or_generate_diagnosis(
        session, graph, env["students"][T01], as_of=date(2025, 9, 1)
    )

    assert generated is True
    snapshot = report.snapshot_json or {}
    assert snapshot.get("as_of", "").startswith("2025-09-01")


# ---------------------------------------------------------------------------
# 无 exam 无 as_of（最近一场 → 今天现算）
# ---------------------------------------------------------------------------


def test_latest_stored_without_params(env, session, graph):
    exam = _exam(session, env)
    session.commit()
    report, _ = get_or_generate_diagnosis(session, graph, env["students"][T01], exam_id=exam.id)
    session.commit()

    # 无参路径：返回最近一场考试的已存诊断
    report2, generated = get_or_generate_diagnosis(session, graph, env["students"][T01])

    assert generated is False
    assert report2.id == report.id


def test_no_params_computes_today_when_none_stored(env, session, graph):
    """无已存诊断 + 无参 -> 按本地今日末现算（兼容旧行为）。"""
    report, generated = get_or_generate_diagnosis(session, graph, env["students"][T01])

    assert generated is True
    assert report.exam_id is None


def test_latest_picks_most_recent_exam(env, session, graph):
    """最近一场 = 考试日期降序的最新一场，而非生成时间最新。"""
    older = _exam(session, env, day=date(2025, 9, 1))
    newer = _exam(session, env, day=date(2025, 10, 1))
    session.commit()
    get_or_generate_diagnosis(session, graph, env["students"][T01], exam_id=older.id)
    get_or_generate_diagnosis(session, graph, env["students"][T01], exam_id=newer.id)
    session.commit()

    report, generated = get_or_generate_diagnosis(session, graph, env["students"][T01])

    assert generated is False
    assert report.exam_id == newer.id


# ---------------------------------------------------------------------------
# narrative 缓存
# ---------------------------------------------------------------------------


def test_narrative_caches_once(env, session, graph):
    exam = _exam(session, env)
    session.commit()
    report, _ = get_or_generate_diagnosis(session, graph, env["students"][T01], exam_id=exam.id)

    # 首次生成时无 LLM（mock 无预设）-> 空串，不写缓存
    first = get_or_create_narrative(session, report)
    assert first == "" or report.narrative_markdown
    if report.narrative_markdown:
        second = get_or_create_narrative(session, report)
        assert second == report.narrative_markdown


def test_narrative_refreshes_stale_prompt_cache(env, session, graph):
    """prompt 升级后替换旧短解读，确保既有报告也能看到新版详细结构。"""
    exam = _exam(session, env)
    session.commit()
    report, _ = get_or_generate_diagnosis(session, graph, env["students"][T01], exam_id=exam.id)
    report.narrative_markdown = "\n## AI 解读（模型生成）\n旧版短解读\n\n_（prompt narrative-v0.2.0）_"

    get_text_breaker().reset()
    mock = MockLLMClient([{"text": "**核心判断**\n新版详细解读"}])
    set_client(mock)
    try:
        refreshed = get_or_create_narrative(session, report)
    finally:
        set_client(None)
        get_text_breaker().reset()

    assert "新版详细解读" in refreshed
    assert f"prompt {NARRATIVE_PROMPT_VERSION}" in refreshed
    assert "旧版短解读" not in refreshed


def test_narrative_keeps_old_cache_when_refresh_fails(env, session, graph):
    """升级时 LLM 不可用不应让已有解读从页面消失。"""
    exam = _exam(session, env)
    session.commit()
    report, _ = get_or_generate_diagnosis(session, graph, env["students"][T01], exam_id=exam.id)
    old = "\n## AI 解读（模型生成）\n旧版仍可阅读\n\n_（prompt narrative-v0.2.0）_"
    report.narrative_markdown = old

    get_text_breaker().reset()
    set_client(MockLLMClient())
    try:
        assert get_or_create_narrative(session, report) == old
    finally:
        set_client(None)
        get_text_breaker().reset()


def test_narrative_cache_signature_requires_delimiter(env, session, graph):
    """签名匹配带定界符：narrative-v0.3 缓存不得被 v0.3.1 判为新鲜（前缀误命中）。"""
    exam = _exam(session, env)
    session.commit()
    report, _ = get_or_generate_diagnosis(session, graph, env["students"][T01], exam_id=exam.id)
    # 构造「当前版本号是既有缓存版本号前缀」的场景：落款里写一个更长的版本号
    longer = NARRATIVE_PROMPT_VERSION + ".1"
    report.narrative_markdown = (
        f"\n## AI 解读（模型生成）\n旧版\n\n_（prompt {longer}；对外使用前请教师预览确认）_"
    )

    get_text_breaker().reset()
    mock = MockLLMClient([{"text": "**核心判断**\n新版"}])
    set_client(mock)
    try:
        refreshed = get_or_create_narrative(session, report)
    finally:
        set_client(None)
        get_text_breaker().reset()

    assert "新版" in refreshed, "前缀版本不得误判新鲜，应触发重新生成"


# ---------------------------------------------------------------------------
# quality get-or-generate
# ---------------------------------------------------------------------------


def test_quality_get_or_generate(env, session, graph):
    exam = _exam(session, env)
    session.commit()

    r1 = get_or_generate_quality_report(session, graph, env["class"].id, exam.id)
    assert r1.type == "quality_analysis"

    r2 = get_or_generate_quality_report(session, graph, env["class"].id, exam.id)
    assert r2.id == r1.id


def test_quality_generate_failure_raises_value_error(env, session, graph, monkeypatch):
    """补生成失败 -> ValueError（端点翻译 400；基础报告纯计算，正常不失败）。"""
    exam = _exam(session, env)
    session.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("模拟计算失败")

    import app.reports.diagnosis_orchestrator as orch

    monkeypatch.setattr(orch, "generate_quality_analysis", boom)
    with pytest.raises(ValueError, match="模拟计算失败"):
        get_or_generate_quality_report(session, graph, env["class"].id, exam.id)
