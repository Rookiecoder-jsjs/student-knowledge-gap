"""LLM 叙述层（DESIGN §15 P2；架构修复 候选3 后为 gateway 的兼容入口）。

职责边界与不变量④ 的守护逻辑已迁至 ``app.llm.gateway``（唯一文本 LLM seam，
含文本熔断器）。本模块保留 ``render_narrative`` 旧签名（``str | None``）供既有
调用方与测试使用：内部委托 ``narrate``，把 ``''`` 归一化为 ``None``。
"""

from __future__ import annotations

from app.llm.gateway import SECTION_HEADER, narrate

__all__ = ["SECTION_HEADER", "render_narrative"]


def render_narrative(report_markdown: str, report_type: str) -> str | None:
    """返回带标注的解读段落；LLM 不可用/失败时返回 None（调用方跳过）。"""
    section = narrate(report_markdown, report_type)
    return section or None
