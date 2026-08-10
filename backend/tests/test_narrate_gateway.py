"""候选3：文本 LLM 闸门（llm/gateway.py）——熔断 → 调用 → 校验 → 标注段落。

- 成功：段落带「模型生成，数字以系统计算为准」标注，且喂给模型的 user prompt
  含确定性报告全文（唯一事实来源，不变量④）；
- LLMError / 熔断开启：降级为 ``''``（不阻塞报告，不变量③），熔断时不触达 provider。
"""

from __future__ import annotations

from app.config import LLM_CB_THRESHOLD
from app.llm.client import LLMError, MockLLMClient, set_client
from app.llm.gateway import SECTION_HEADER, get_text_breaker, narrate

DETERMINISTIC_MD = "# 报告\n掌握度约 45%，证据 3 题。\n"


def test_narrate_success_appends_labeled_section():
    get_text_breaker().reset()
    mock = MockLLMClient([{"text": "该生需先补绝对值。"}])
    set_client(mock)
    try:
        out = narrate(DETERMINISTIC_MD, "student_diagnosis")
        assert out.startswith(SECTION_HEADER)
        assert "模型生成" in out and "教师预览" in out
        # prompt 里带上了确定性报告全文作为唯一事实来源
        assert DETERMINISTIC_MD.strip() in mock.calls[0]["user"]
    finally:
        set_client(None)
        get_text_breaker().reset()


def test_narrate_degrade_on_llm_error_returns_empty():
    get_text_breaker().reset()

    class Boom(MockLLMClient):
        def parse_json(self, system, user, image_bytes):
            raise LLMError("boom")

    set_client(Boom())
    try:
        assert narrate(DETERMINISTIC_MD, "quality_analysis") == ""
    finally:
        set_client(None)
        get_text_breaker().reset()


def test_narrate_fast_fail_when_breaker_open():
    get_text_breaker().reset()
    mock = MockLLMClient([{"text": "x"}])
    set_client(mock)
    try:
        breaker = get_text_breaker()
        for _ in range(LLM_CB_THRESHOLD):
            breaker.record_failure()
        assert breaker.state == "open"
        assert narrate(DETERMINISTIC_MD, "student_diagnosis") == ""
        assert len(mock.calls) == 0, "熔断开启时快速拒绝，不触达 provider"
    finally:
        set_client(None)
        get_text_breaker().reset()


def test_narrate_records_success_after_ok_call():
    """成功调用后熔断器复位（后续失败重新计数）。"""
    get_text_breaker().reset()
    mock = MockLLMClient([{"text": "ok"}])
    set_client(mock)
    try:
        narrate(DETERMINISTIC_MD, "student_diagnosis")
        assert get_text_breaker().state == "closed"
    finally:
        set_client(None)
        get_text_breaker().reset()
