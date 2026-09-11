"""学习方案 LLM 生成层（study-loop-design）：调用 → 校验 → 回落模板。

与 plan_writer 同纪律（照抄骨架）：开关 ``config.STUDY_PLAN_ENABLE``（默认关，
未配 provider 时 ``get_client`` 抛错走同一回落路径）；独立熔断器互不拖累；
校验失败不计 provider 失败（不动熔断计数）；任何失败返回 ``None``，
调用方保留模板正文（writer = {"template": true}），自报闭环不断。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import app.config as config
from app.llm.audit import audit_context, record_circuit_open
from app.llm.circuit import CircuitBreaker, CircuitOpenError
from app.llm.client import get_client
from app.llm.prompts import (
    STUDY_PLAN_SYSTEM,
    STUDY_PROMPT_VERSION,
    study_plan_user_prompt,
)

_RANK_PAT = re.compile(r"排名|第\s*\d+\s*名")
_LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.、)])\s*(.+)$")
_REQUIRED_SECTIONS = ("先补这一步", "核心讲解", "针对你的练习", "怎么确认自己学会了")


@dataclass
class StudyDraft:
    """LLM 生成的学习方案草稿（校验已通过）；model 供 plan_writer 溯源。"""

    markdown: str
    model: str


# 报告叙述 / 视觉解析 / plan_writer / 本生成层各自独立熔断。
_study_breaker = CircuitBreaker()


def get_study_breaker() -> CircuitBreaker:
    return _study_breaker


def _validate_study_plan(md: str) -> bool:
    """学习方案：四结构段齐全；练习段 ≥2 道例题；篇幅有界；无排名。"""
    if not md or len(md) > 4000:
        return False
    if any(seg not in md for seg in _REQUIRED_SECTIONS):
        return False
    practice_seg = md.split("针对你的练习", 1)[-1].split("怎么确认自己学会了", 1)[0]
    items = [
        ln for ln in practice_seg.splitlines() if _LIST_ITEM.match(ln.lstrip())
    ]
    if len(items) < 2:
        return False
    return _RANK_PAT.search(md) is None


def _call_llm(pack: dict) -> tuple[object, dict] | None:
    """单次调用（熔断/审计在内）；provider 故障或熔断返回 None。"""
    try:
        _study_breaker.before_call()
        client = get_client("text")
        with audit_context("study_plan", STUDY_PROMPT_VERSION):
            payload = client.parse_json(
                STUDY_PLAN_SYSTEM, study_plan_user_prompt(pack), None
            )
        _study_breaker.record_success()
        return client, payload
    except CircuitOpenError as e:
        record_circuit_open("text", f"study_writer: {e}")
        return None
    except Exception:  # noqa: BLE001 —— LLMError/httpx/JSON 解析等一律回落模板
        _study_breaker.record_failure()
        return None


def write_study_plan(pack: dict) -> StudyDraft | None:
    """学习方案正文：证据包 → LLM → 校验；关闭/失败/不合格均返回 None（保模板）。

    校验失败重试一次（flash 级模型偶发空正文/漏段落，重试通常恢复）——
    那不是 provider 故障，不动熔断计数；provider 故障/熔断不重试。
    """
    if not config.STUDY_PLAN_ENABLE:
        return None
    for _ in range(2):
        out = _call_llm(pack)
        if out is None:
            return None
        client, payload = out
        md = ""
        if isinstance(payload, dict):
            md = str(payload.get("markdown") or "").strip()
        if md and _validate_study_plan(md):
            return StudyDraft(markdown=md, model=client.model_version)
    return None
