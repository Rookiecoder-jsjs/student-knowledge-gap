"""文本 LLM 闸门（架构修复 候选3：强化不变量③④）。

唯一文本 LLM seam：熔断 → 调用 → 校验 → 返回带标注段落；不可用返回 ``''``。
- 不变量③（降级不阻塞）：LLM 失败/熔断/未配置 → 静默返回空串，报告保持纯模板；
- 不变量④（数字零幻觉）：段落显式标注「模型生成，数字以系统计算为准」，
  且 prompt 只允许模型引用确定性报告里的数字（`NARRATIVE_SYSTEM`「铁律」）。

``narrative.py`` 的 ``render_narrative`` 已退为对本模块 ``narrate`` 的兼容委托。
"""

from __future__ import annotations

from app.llm.circuit import CircuitBreaker, CircuitOpenError
from app.llm.client import LLMError, get_client
from app.llm.prompts import (
    NARRATIVE_PROMPT_VERSION,
    NARRATIVE_SYSTEM,
    narrative_user_prompt,
)

SECTION_HEADER = "\n## AI 解读（模型生成，数字以系统计算为准）\n"

# 文本报告叙述与视觉解析（batch 拍照）各自独立熔断，互不拖累。
_text_breaker = CircuitBreaker()


def get_text_breaker() -> CircuitBreaker:
    return _text_breaker


def _coerce_text(payload) -> str:
    """叙述任务允许模型直接输出纯文本或 {"text": ...}，两者都接受。"""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("text", "content", "summary", "narrative"):
            if isinstance(payload.get(key), str):
                return payload[key]
    return ""


def narrate(report_markdown: str, report_type: str) -> str:
    """唯一文本 LLM seam：熔断 → 调用 → 校验 → 返回带标注段落；不可用返回 ``''``。

    ``report_markdown`` 是确定性报告全文，作为唯一事实来源喂给模型
    （不变量④：数字不由模型产生，只允许逐字引用材料）。
    """
    try:
        _text_breaker.before_call()
        client = get_client("text")
        payload = client.parse_json(
            NARRATIVE_SYSTEM,
            narrative_user_prompt(report_markdown, report_type),
            None,
        )
        _text_breaker.record_success()
    except (LLMError, CircuitOpenError):
        _text_breaker.record_failure()
        return ""

    text = _coerce_text(payload)
    if not text:
        return ""
    return (
        SECTION_HEADER
        + text.strip()
        + f"\n\n_（由 {client.model_version} 生成，prompt {NARRATIVE_PROMPT_VERSION}；"
        "对外使用前请教师预览确认）_"
    )
