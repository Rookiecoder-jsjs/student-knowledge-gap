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
