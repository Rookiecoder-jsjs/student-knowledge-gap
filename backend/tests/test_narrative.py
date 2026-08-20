"""LLM 叙述层：数字仍由系统注入，模型只写解读；失败时静默降级。"""

from __future__ import annotations

from app.llm.client import LLMError, MockLLMClient, set_client
from app.reports.narrative import SECTION_HEADER, render_narrative

DETERMINISTIC_MD = "# 报告\n掌握度约 45%，证据 3 题。\n"


def test_narrative_appends_labeled_section():
    mock = MockLLMClient([{"text": "该生在绝对值上需要关注，建议先补前置。"}])
    set_client(mock)
    try:
        out = render_narrative(DETERMINISTIC_MD, "student_diagnosis")
        assert out is not None and out.startswith(SECTION_HEADER)
        assert "模型生成" in out and "教师预览" in out
        assert "绝对值" in out
        # prompt 里带上了确定性报告全文作为唯一事实来源
        assert DETERMINISTIC_MD.strip() in mock.calls[0]["user"]
    finally:
        set_client(None)


def test_narrative_graceful_degrade_on_llm_error():
    class Boom(MockLLMClient):
        def parse_json(self, system, user, image_bytes):
            raise LLMError("boom")

    set_client(Boom())
    try:
        assert render_narrative(DETERMINISTIC_MD, "student_diagnosis") is None
    finally:
        set_client(None)


def test_narrative_degrade_when_unconfigured(monkeypatch):
    """provider=mock（未配置真实密钥）→ 返回 None，报告保持纯模板。"""
    set_client(None)
    monkeypatch.delenv("SC_LLM_PROVIDER", raising=False)
    assert render_narrative(DETERMINISTIC_MD, "quality_analysis") is None


def test_narrative_prompt_differs_by_report_type():
    """两种报告给差异化重点指引，不共用同一 user prompt。"""
    mock = MockLLMClient([{"text": "x"}, {"text": "y"}])
    set_client(mock)
    try:
        render_narrative(DETERMINISTIC_MD, "student_diagnosis")
        render_narrative(DETERMINISTIC_MD, "quality_analysis")
    finally:
        set_client(None)
    u_stu, u_cls = mock.calls[0]["user"], mock.calls[1]["user"]
    assert u_stu != u_cls
    assert "地基点" in u_stu and "可能原因" in u_stu
    assert "共性" in u_cls and "集体教学" in u_cls


def test_narrative_system_relaxes_verbatim_and_keeps_guardrails():
    """放宽'逐字引用'为'不引入材料外数字'，并保留不编造/不排名/归因假设等硬约束。"""
    mock = MockLLMClient([{"text": "x"}])
    set_client(mock)
    try:
        render_narrative(DETERMINISTIC_MD, "student_diagnosis")
    finally:
        set_client(None)
    sys_prompt = mock.calls[0]["system"]
    assert "逐字" not in sys_prompt, "不再要求逐字引用（避免模型拒引数字导致空洞）"
    assert "不得引入材料里没有" in sys_prompt
    assert "建议教师核实" in sys_prompt  # 归因仍表述为假设
    assert "无进步点则不勉强" in sys_prompt  # 成长框架兜底
    assert "排名" in sys_prompt  # 不排名约束保留
    assert "**核心判断**" in sys_prompt
    assert "**关键证据**" in sys_prompt
    assert "**优先行动**" in sys_prompt
    assert "**验证与边界**" in sys_prompt
    assert "350~550" in sys_prompt and "450~700" in sys_prompt
