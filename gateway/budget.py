"""预算护栏三道闸（agent-product-design §5.7，Phase 3 批次C）。

| 闸 | 默认值 | 超限行为 |
|---|---|---|
| 单 Task 轮数上限 | 12 | 拒绝新 turn（优雅收尾：已完成调查照常呈现）|
| 单 Task token 预算 | ~200K tokens | 同上 + 运行中 turn 自动 interrupt |
| 月度软限额 | 可配置 | 80% 时触发通知钩子（仅提醒不断供）|

「Task」在网关语境 = 同一班级持久线程上的一次 turn 序列（触发式考后分析
或教师交互会话）。轮数/token 按 thread 计数，线程空闲超过 TASK_IDLE_TTL
视为新 Task 开始重新计——持久线程跨学期滚动，不能按线程生命周期算 Task。

设计约束：
- 护栏是网关职责（§5.7 第一天就有），壳零核改；sc 零感知；
- token 累计消费 app-server 的 thread/tokenUsage/updated 通知（FINDINGS F5，
  total.total_tokens 为累计口径），超预算时对运行中线程发 turn/interrupt；
- 月度软限额读 sc 后端 /admin/usage/summary（学校自持 key、自担消费，
  「看得见花在哪」的信任基建延伸）；超限只通知不拦截（硬限额开关留学校，
  本期不实现硬闸）。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# 配置（默认值=设计文档 §5.7 试点基线，实测后修订）
# ---------------------------------------------------------------------------

MAX_TURNS_PER_TASK = _env_int("SC_BUDGET_MAX_TURNS", 12)
MAX_TOKENS_PER_TASK = _env_int("SC_BUDGET_MAX_TOKENS", 200_000)
TASK_IDLE_TTL_S = float(os.environ.get("SC_BUDGET_TASK_IDLE_TTL", "1800"))  # 30 分钟
MONTHLY_SOFT_LIMIT_TOKENS = _env_int("SC_BUDGET_MONTHLY_LIMIT", 0)  # 0=不限
MONTHLY_NOTIFY_RATIO = float(os.environ.get("SC_BUDGET_MONTHLY_NOTIFY_RATIO", "0.8"))


@dataclass
class TaskBudget:
    """一个线程当前 Task 的用量账本。"""

    turns: int = 0
    total_tokens: int = 0
    last_activity: float = field(default_factory=time.monotonic)

    def stale(self) -> bool:
        return (time.monotonic() - self.last_activity) > TASK_IDLE_TTL_S


class BudgetExceeded(Exception):
    """超限拒绝（message 面向教师可读，由 /rpc 402 回传）。"""

    def __init__(self, reason: str, usage: dict):
        super().__init__(reason)
        self.reason = reason
        self.usage = usage


class BudgetGuard:
    """进程内护栏状态机。线程键用 thread_id（app-server 命名空间全局唯一）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskBudget] = {}
        # 月度软限额通知去重：每 (month, ratio_level) 只发一次
        self._notified: set[str] = set()

    # ---- 轮数闸 -----------------------------------------------------------

    def check_turn_start(self, thread_id: str) -> dict:
        """turn/start 前调用：超限抛 BudgetExceeded，否则记账 +1 轮。

        空闲超时的旧 Task 视为自然收尾，重开新账（教师第二天回来是新的工作）。
        """
        self._gc()
        b = self._tasks.get(thread_id)
        if b is None or b.stale():
            b = TaskBudget()
            self._tasks[thread_id] = b
        if b.turns >= MAX_TURNS_PER_TASK:
            raise BudgetExceeded(
                f"本次分析已连续进行 {b.turns} 轮，达到单任务上限 {MAX_TURNS_PER_TASK} 轮。"
                "已完成的部分照常保留；请稍作整理后再开始新的分析。",
                {"thread_id": thread_id, "turns": b.turns,
                 "total_tokens": b.total_tokens},
            )
        if MAX_TOKENS_PER_TASK and b.total_tokens >= MAX_TOKENS_PER_TASK:
            raise BudgetExceeded(
                f"本次分析已消耗约 {b.total_tokens} tokens（预算 {MAX_TOKENS_PER_TASK}）。"
                "已完成的部分照常保留；请整理结论后另起分析。",
                {"thread_id": thread_id, "turns": b.turns,
                 "total_tokens": b.total_tokens},
            )
        b.turns += 1
        b.last_activity = time.monotonic()
        return {"turns": b.turns, "total_tokens": b.total_tokens}

    # ---- token 闸 ---------------------------------------------------------

    def observe_token_usage(self, thread_id: str, total_tokens: int) -> dict | None:
        """消费 thread/tokenUsage/updated 通知；返回 interrupt 决定（None=放行）。"""
        b = self._tasks.get(thread_id)
        if b is None:
            b = TaskBudget()
            self._tasks[thread_id] = b
        b.total_tokens = max(b.total_tokens, int(total_tokens))  # total 是累计口径
        b.last_activity = time.monotonic()
        if MAX_TOKENS_PER_TASK and b.total_tokens >= MAX_TOKENS_PER_TASK:
            return {
                "action": "interrupt",
                "thread_id": thread_id,
                "total_tokens": b.total_tokens,
                "budget": MAX_TOKENS_PER_TASK,
                "reason": (
                    f"token 预算 {MAX_TOKENS_PER_TASK} 已用尽（实际 {b.total_tokens}），"
                    "本轮分析自动收尾；已完成部分照常保留"
                ),
            }
        return None

    # ---- 月度软限额 -------------------------------------------------------

    def check_monthly(self, summary: dict) -> dict | None:
        """传入 sc /admin/usage/summary 输出；达阈值返回通知载荷（每次额度只发一次）。

        summary 形如 {"month": "...", "total_prompt_tokens": n, "total_completion_tokens": m}
        （admin_usage.usage_summary_month；无计量历史行不计入 → 合计可能为 0）。
        """
        if not MONTHLY_SOFT_LIMIT_TOKENS:
            return None
        used = int(summary.get("total_prompt_tokens") or 0) + int(
            summary.get("total_completion_tokens") or 0
        )
        month = summary.get("month") or ""
        level = None
        if used >= MONTHLY_SOFT_LIMIT_TOKENS:
            level = "exceeded"
        elif used >= MONTHLY_SOFT_LIMIT_TOKENS * MONTHLY_NOTIFY_RATIO:
            level = "80pct"
        if level is None:
            return None
        key = f"{month}:{level}"
        if key in self._notified:
            return None
        self._notified.add(key)
        return {
            "level": level,
            "month": month,
            "used_tokens": used,
            "limit": MONTHLY_SOFT_LIMIT_TOKENS,
            "ratio": round(used / MONTHLY_SOFT_LIMIT_TOKENS, 3),
            "message": (
                f"本月模型消耗已达月度限额的 {round(used / MONTHLY_SOFT_LIMIT_TOKENS * 100)}%"
                if level == "80pct"
                else "本月模型消耗已超出月度软限额（服务不受影响）"
            ),
        }

    # ---- 内部 -------------------------------------------------------------

    def _gc(self) -> None:
        for tid in [t for t, b in self._tasks.items() if b.stale()]:
            del self._tasks[tid]

    def snapshot(self) -> dict:
        self._gc()
        return {tid: {"turns": b.turns, "total_tokens": b.total_tokens}
                for tid, b in self._tasks.items()}


GUARD = BudgetGuard()
