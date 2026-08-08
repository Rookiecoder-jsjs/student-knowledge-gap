"""LLM 叙述层（DESIGN §15 P2 提前落地；不变量④的守护方式见下）。

职责边界：确定性报告是唯一事实来源，LLM 只把已算好的结论改写成
教师易读的解读文字。数字不由模型产生——模型仅被允许逐字引用材料中的数字，
且输出段落显式标注"模型生成，数字以上文系统计算为准"。
LLM 不可用/失败时静默降级为纯模板报告（不阻塞主流程）。
"""

from __future__ import annotations

from app.llm.client import LLMError, get_client
from app.llm.prompts import (
    NARRATIVE_PROMPT_VERSION,
    NARRATIVE_SYSTEM,
    narrative_user_prompt,
)

SECTION_HEADER = "\n## AI 解读（模型生成，数字以系统计算为准）\n"


def render_narrative(report_markdown: str, report_type: str) -> str | None:
    """返回带标注的解读段落；LLM 不可用时返回 None（调用方跳过）。"""
    try:
        client = get_client("text")
    except LLMError:
        return None
    try:
        payload = client.parse_json(
            NARRATIVE_SYSTEM,
            narrative_user_prompt(report_markdown, report_type),
            None,
        )
    except LLMError:
        return None

    text = _coerce_text(payload)
    if not text:
        return None
    return (
        SECTION_HEADER
        + text.strip()
        + f"\n\n_（由 {client.model_version} 生成，prompt {NARRATIVE_PROMPT_VERSION}；"
        "对外使用前请教师预览确认）_"
    )


def _coerce_text(payload) -> str:
    """叙述任务允许模型直接输出纯文本或 {"text": ...}，两者都接受。"""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("text", "content", "summary", "narrative"):
            if isinstance(payload.get(key), str):
                return payload[key]
    return ""
