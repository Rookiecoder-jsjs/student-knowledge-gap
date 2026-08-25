"""月度软限额巡查（agent-product-design §5.7 第三闸 + §5.9 台账联动）。

网关侧后台任务：周期性读 sc /admin/usage/summary，达 80% 阈值时触发
通知钩子（钉钉等；钩子在 main.py 启动时经 register_monthly_notify_hook
注册）。学校自持 key 自担消费——「仅提醒不断供」，硬限额开关留学校。

独立模块便于测试（不依赖 FastAPI 运行时）。
"""

from __future__ import annotations

import os
import time
from typing import Callable

import httpx

NotifyHook = Callable[[dict], None]
_hooks: list[NotifyHook] = []

CHECK_INTERVAL_S = float(os.environ.get("SC_BUDGET_MONTHLY_CHECK_S", "3600"))


def register_monthly_notify_hook(hook: NotifyHook) -> None:
    """注册通知钩子（幂等；钉钉/日志等在启动时注册）。"""
    if hook not in _hooks:
        _hooks.append(hook)


def _dispatch(payload: dict) -> None:
    for h in list(_hooks):
        try:
            h(payload)
        except Exception as e:  # noqa: BLE001 —— 通知失败不影响巡查循环
            print(f"[monthly-usage] notify hook failed: {e}")


def fetch_usage_summary(base_url: str, timeout: float = 8.0) -> dict | None:
    """拉 sc 用量摘要；不可达返回 None（下轮再查——sc 断供不产生误报）。"""
    try:
        with httpx.Client(timeout=timeout) as hc:
            r = hc.get(f"{base_url.rstrip('/')}/admin/usage/summary")
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:  # noqa: BLE001
        return None


def check_once(summary: dict | None) -> dict | None:
    """单次检查：GUARD.check_monthly 裁决 + 钩子分发。返回通知载荷或 None。"""
    from gateway.budget import GUARD

    if summary is None:
        return None
    notice = GUARD.check_monthly(summary)
    if notice is not None:
        _dispatch(notice)
    return notice


async def monthly_watch_loop(stop_flag: list) -> None:
    """后台巡查循环（main.py lifespan 启动；stop_flag[0]=True 退出）。

    SC_BACKEND_URL 未配置时静默空转——单机 compose 里默认同网段可达。
    """
    import asyncio

    base = os.environ.get("SC_BACKEND_URL", "")
    while not stop_flag[0]:
        if base:
            summary = await asyncio.to_thread(fetch_usage_summary, base)
            check_once(summary)
        # 分片 sleep，退出响应快
        waited = 0.0
        while waited < CHECK_INTERVAL_S and not stop_flag[0]:
            await asyncio.sleep(min(5.0, CHECK_INTERVAL_S - waited))
            waited += 5.0


def reset_for_tests() -> None:
    _hooks.clear()
