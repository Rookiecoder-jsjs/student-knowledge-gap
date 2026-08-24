"""诊断单 LLM 生成层（diagnosis-sheet-redesign.md §2.5）：调用 → 校验 → 回落。

职责：拿证据包喂 LLM 写正文，结构校验失败**整体回落模板**（不做局部修补）。
- 开关：``config.LLM_PLAN_ENABLE``（默认关）；未配 key（provider=mock 且无测试注入）
  时 ``get_client`` 直接抛错 → 同一回落路径，自动等同关闭；
- 熔断：与文本叙述/视觉解析各自独立（``_plan_breaker``），互不拖累；
- 校验失败不计 provider 失败（不动熔断计数）——那不是 provider 的错；
- 前端不感知 LLM 存在与否：返回 ``None`` 即保留模板正文（writer 缺省 = 模板）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import app.config as config
from app.kb.graph import KpGraph
from app.llm.audit import audit_context, record_circuit_open
from app.llm.circuit import CircuitBreaker, CircuitOpenError
from app.llm.client import get_client
from app.llm.prompts import (
    CLASS_ADVICE_SYSTEM,
    PLAN_PROMPT_VERSION,
    STUDENT_DIAGNOSIS_SYSTEM,
    class_advice_user_prompt,
    student_diagnosis_user_prompt,
)
from app.reports.diagnosis_model import DiagnosisReportModel
from app.reports.evidence_pack import class_evidence_pack, student_evidence_pack
from app.reports.quality_model import QualityReportModel

# 排名/比较表述一律禁止（README 设计约束：无排名）
_RANK_PAT = re.compile(r"排名|第\s*\d+\s*名")


@dataclass
class PlanDraft:
    """LLM 生成的正文草稿（校验已通过）；model 供 snapshot_json.writer 溯源。"""

    markdown: str
    model: str


# 文本叙述（narrate）/ 视觉解析 / 本生成层三者独立熔断。
_plan_breaker = CircuitBreaker()


def get_plan_breaker() -> CircuitBreaker:
    return _plan_breaker


# ---------------------------------------------------------------------------
# 校验（§2.5：结构失败整体回落，不做局部修补）
# ---------------------------------------------------------------------------


def _validate_student_diagnosis(md: str) -> bool:
    """诊断单：非空、含「保持与进步」「下一步」结构段、无排名、篇幅有界。"""
    if not md or len(md) > 1500:
        return False
    if "保持与进步" not in md or "下一步" not in md:
        return False
    return _RANK_PAT.search(md) is None


# 列表项前缀：无序（- * +）与有序（1. 2.）都算——模型两种写法都会输出，
# 内容合格但格式不同不该触发整体回落
_LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.、)])\s*(.+)$")


def _list_items(md: str) -> list[str]:
    out = []
    for ln in md.splitlines():
        m = _LIST_ITEM.match(ln.lstrip())
        if m and m.group(1).strip():
            out.append(m.group(1).strip())
    return out


def _validate_class_advice(md: str, pack: dict, forbidden_names: list[str] | None) -> bool:
    """班级改进意见：条目 3~5；每条能对上证据包中的知识点名；无学生姓名；≤300 字级。"""
    if not md or len(md) > 600:
        return False
    items = _list_items(md)
    if not (3 <= len(items) <= 5):
        return False
    known_kps = {d["kp"] for d in pack.get("common_weak", []) if d.get("kp")}
    for q in pack.get("low_rate_questions", []):
        known_kps.update(k for k in re.split(r"[、,，]\s*", q.get("kps") or "") if k)
    # 逐条锚定证据包中的 kp 名（防编造）；证据包本身无 kp（平稳班级）时不强求，
    # 否则「证据不足」的诚实回答永远过不了校验、只能回落模板
    if known_kps and not all(any(kp in it for kp in known_kps) for it in items):
        return False
    for it in items:
        if len(it) > 100:
            return False
    joined = md
    if _RANK_PAT.search(joined):
        return False
    return not any(n and n in joined for n in (forbidden_names or []))


# ---------------------------------------------------------------------------
# 生成入口（best-effort：任何失败返回 None，调用方保留模板正文）
# ---------------------------------------------------------------------------


def _write(system: str, user: str, validate) -> PlanDraft | None:
    try:
        _plan_breaker.before_call()
        client = get_client("text")
        with audit_context("plan_writer", PLAN_PROMPT_VERSION):
            payload = client.parse_json(system, user, None)
        _plan_breaker.record_success()
    except CircuitOpenError as e:
        record_circuit_open("text", f"plan_writer: {e}")
        return None
    except Exception:  # noqa: BLE001 —— LLMError/httpx/JSON 解析等一律回落模板
        _plan_breaker.record_failure()
        return None

    md = ""
    if isinstance(payload, dict):
        md = str(payload.get("markdown") or "").strip()
    if not md or not validate(md):
        # 质量不合格 ≠ provider 故障：不动熔断计数，整体回落模板
        return None
    return PlanDraft(markdown=md, model=client.model_version)


def write_student_diagnosis(
    graph: KpGraph, model: DiagnosisReportModel
) -> PlanDraft | None:
    """学生诊断单正文：证据包 → LLM → 校验；关闭/失败/不合格均返回 None（保模板）。"""
    if not config.LLM_PLAN_ENABLE:
        return None
    pack = student_evidence_pack(graph, model, kind="student_diagnosis")
    return _write(
        STUDENT_DIAGNOSIS_SYSTEM,
        student_diagnosis_user_prompt(pack),
        _validate_student_diagnosis,
    )


def write_class_advice(
    graph: KpGraph,
    quality: QualityReportModel,
    as_of=None,
    trend_summary: dict | None = None,
    forbidden_names: list[str] | None = None,
) -> PlanDraft | None:
    """班级改进意见：聚合证据包 → LLM → 校验；forbidden_names 为全班名单（防姓名泄漏）。"""
    if not config.LLM_PLAN_ENABLE:
        return None
    pack = class_evidence_pack(graph, quality, as_of=as_of, trend_summary=trend_summary)
    return _write(
        CLASS_ADVICE_SYSTEM,
        class_advice_user_prompt(pack),
        lambda md: _validate_class_advice(md, pack, forbidden_names),
    )
