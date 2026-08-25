"""用量台账 v1（agent-product-design §5.9，Phase 2 批次D）。

「学校自己付钱，就必须看得见花在哪」——llm_call_log 的 token 两列按
task/日 聚合，管理端「本月消耗」页数据源。

口径说明：
- 只统计 status='success' 的行（error/circuit_open 未触达或失败，不产生
  有效消耗；provider 计费以成功调用为准）；
- mock 路径 usage=None（记 0 展示），历史行 NULL 同样按 0 计入调用数；
- task 取值沿用审计层既有词表（narrative/tagger/batch_parse/template_parse/
  response_parse/plan_writer）；壳侧循环内调用的 agent_turn 等记在网关侧，
  本台账只覆盖 sc 直连 LLM 的部分（§5.9 注记）。
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import LlmCallLog


def _month_range(month: str) -> tuple[date, date]:
    """'YYYY-MM' → (首日, 次月首日)。非法格式抛 ValueError。"""
    try:
        y, m = month.split("-")
        first = date(int(y), int(m), 1)
    except (ValueError, AttributeError) as e:
        raise ValueError(f"月份格式应为 YYYY-MM：{month!r}") from e
    if m == "12":
        nxt = date(first.year + 1, 1, 1)
    else:
        nxt = date(first.year, int(m) + 1, 1)
    return first, nxt


def usage_by_day_task(session: Session, month: str) -> dict:
    """某月按 (task, 日) 聚合的 token 与调用数 + 任务小计。

    返回 {month, days: [{date, by_task: {task: {...}}, totals}], by_task 小计,
    total}；token 为 NULL（mock/历史行）按 0 计。
    """
    first, nxt = _month_range(month)
    prompt_sum = func.coalesce(func.sum(LlmCallLog.prompt_tokens), 0)
    completion_sum = func.coalesce(func.sum(LlmCallLog.completion_tokens), 0)
    day = func.date(LlmCallLog.at)

    rows = session.execute(
        select(
            day.label("d"),
            LlmCallLog.task,
            func.count(LlmCallLog.id).label("calls"),
            prompt_sum,
            completion_sum,
        )
        .where(
            LlmCallLog.at >= first,
            LlmCallLog.at < nxt,
            LlmCallLog.status == "success",
        )
        .group_by(day, LlmCallLog.task)
        .order_by(day)
    ).all()

    days: dict[str, dict] = {}
    for d, task, calls, pt, ct in rows:
        ds = str(d)
        bucket = days.setdefault(ds, {"date": ds, "by_task": {}, "totals": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}})
        bucket["by_task"][task] = {
            "calls": calls,
            "prompt_tokens": pt or 0,
            "completion_tokens": ct or 0,
        }
        bucket["totals"]["calls"] += calls
        bucket["totals"]["prompt_tokens"] += pt or 0
        bucket["totals"]["completion_tokens"] += ct or 0

    # 任务小计（整月）
    task_rows = session.execute(
        select(
            LlmCallLog.task,
            func.count(LlmCallLog.id),
            prompt_sum,
            completion_sum,
        )
        .where(
            LlmCallLog.at >= first,
            LlmCallLog.at < nxt,
            LlmCallLog.status == "success",
        )
        .group_by(LlmCallLog.task)
    ).all()
    by_task = {
        task: {"calls": c, "prompt_tokens": p or 0, "completion_tokens": t or 0}
        for task, c, p, t in task_rows
    }
    total = {
        "calls": sum(v["calls"] for v in by_task.values()),
        "prompt_tokens": sum(v["prompt_tokens"] for v in by_task.values()),
        "completion_tokens": sum(v["completion_tokens"] for v in by_task.values()),
    }
    return {
        "month": month,
        "days": [days[k] for k in sorted(days)],
        "by_task": dict(sorted(by_task.items())),
        "total": total,
    }


def usage_summary_month(session: Session, month: str) -> dict:
    """角标/摘要形状：仅整月合计与最大消耗任务。"""
    data = usage_by_day_task(session, month)
    top_task = max(data["by_task"].items(), key=lambda kv: kv[1]["prompt_tokens"], default=None)
    return {
        "month": month,
        "total": data["total"],
        "top_task": {"task": top_task[0], **top_task[1]} if top_task else None,
    }
