"""预算护栏三道闸测试（agent-product-design §5.7，Phase 3 批次C）。

纯逻辑测试：轮数闸 / token 闸 / 月度软限额通知，全部不依赖 FastAPI 运行时。
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 仓库根（gateway 包）

from gateway import monthly_usage
from gateway.budget import BudgetGuard

# 注意：fixture 会重载 gateway.budget 以应用 env 常量，异常类必须动态引用
# （模块级 from-import 会在 reload 后指向旧类对象）。


@pytest.fixture()
def budget_mod(monkeypatch):
    monkeypatch.setenv("SC_BUDGET_MAX_TURNS", "3")
    monkeypatch.setenv("SC_BUDGET_MAX_TOKENS", "1000")
    monkeypatch.setenv("SC_BUDGET_MONTHLY_LIMIT", "10000")
    monkeypatch.delenv("SC_BUDGET_TASK_IDLE_TTL", raising=False)
    import importlib

    from gateway import budget

    importlib.reload(budget)
    yield budget
    importlib.reload(budget)  # 还原默认值


@pytest.fixture()
def guard(budget_mod):
    monthly_usage.reset_for_tests()
    return budget_mod.BudgetGuard()


# ---------------------------------------------------------------------------
# 轮数闸
# ---------------------------------------------------------------------------


def test_turn_gate_blocks_after_limit(guard, budget_mod):
    for i in range(3):
        out = guard.check_turn_start("t1")
        assert out["turns"] == i + 1
    with pytest.raises(budget_mod.BudgetExceeded) as ei:
        guard.check_turn_start("t1")
    assert "单任务上限" in ei.value.reason


def test_new_task_after_idle_resets(guard, monkeypatch):
    for _ in range(3):
        guard.check_turn_start("t2")
    # 空闲 TTL 过期 → 新 Task 重开账本
    guard._tasks["t2"].last_activity -= 3600
    out = guard.check_turn_start("t2")
    assert out["turns"] == 1 and out["total_tokens"] == 0


def test_threads_counted_independently(guard):
    guard.check_turn_start("a")
    guard.check_turn_start("b")
    assert guard.snapshot()["a"]["turns"] == 1
    assert guard.snapshot()["b"]["turns"] == 1


# ---------------------------------------------------------------------------
# token 闸
# ---------------------------------------------------------------------------


def test_token_gate_interrupts_at_budget(guard):
    assert guard.observe_token_usage("t3", 500) is None
    verdict = guard.observe_token_usage("t3", 1100)
    assert verdict is not None and verdict["action"] == "interrupt"
    assert verdict["total_tokens"] == 1100


def test_token_accumulation_monotonic(guard):
    guard.observe_token_usage("t4", 900)
    # total 口径是累计——较小的后续读数不回退
    guard.observe_token_usage("t4", 400)
    assert guard.snapshot()["t4"]["total_tokens"] == 900


def test_turn_gate_blocks_on_tokens_even_with_turns_left(guard, budget_mod):
    guard.check_turn_start("t5")
    guard.observe_token_usage("t5", 1500)
    with pytest.raises(budget_mod.BudgetExceeded) as ei:
        guard.check_turn_start("t5")
    assert "tokens" in ei.value.reason


# ---------------------------------------------------------------------------
# 月度软限额（仅提醒不断供）
# ---------------------------------------------------------------------------


def test_monthly_notify_at_80pct_once(guard):
    summary = {"month": "2026-08", "total_prompt_tokens": 8500,
               "total_completion_tokens": 500}
    notice = guard.check_monthly(summary)
    assert notice is not None and notice["level"] == "80pct"
    # 同级只发一次
    assert guard.check_monthly(summary) is None
    # 升级到超限再发一次
    summary2 = {"month": "2026-08", "total_prompt_tokens": 12000,
                "total_completion_tokens": 0}
    notice2 = guard.check_monthly(summary2)
    assert notice2 is not None and notice2["level"] == "exceeded"


def test_monthly_silent_below_threshold(guard):
    assert guard.check_monthly({"month": "2026-08",
                                "total_prompt_tokens": 100,
                                "total_completion_tokens": 0}) is None


def test_monthly_disabled_when_no_limit(monkeypatch):
    monkeypatch.setenv("SC_BUDGET_MONTHLY_LIMIT", "0")
    import importlib

    from gateway import budget

    importlib.reload(budget)
    try:
        g = budget.BudgetGuard()
        assert g.check_monthly({"month": "2026-08",
                                "total_prompt_tokens": 10**9,
                                "total_completion_tokens": 0}) is None
    finally:
        importlib.reload(budget)


def test_check_once_dispatches_hooks(budget_mod):
    got = []
    monthly_usage.register_monthly_notify_hook(got.append)
    notice = monthly_usage.check_once({
        "month": "2026-08", "total_prompt_tokens": 9500,
        "total_completion_tokens": 600,
    })
    assert notice is not None
    assert got == [notice]
    monthly_usage.reset_for_tests()


def test_fetch_usage_summary_handles_bad_url(monkeypatch):
    monkeypatch.setattr(
        monthly_usage.httpx.Client, "__enter__",
        lambda self: self, raising=False,
    )
    # 不可达端口 → None（sc 断供不产生误报）
    assert monthly_usage.fetch_usage_summary("http://127.0.0.1:1", timeout=0.2) is None
