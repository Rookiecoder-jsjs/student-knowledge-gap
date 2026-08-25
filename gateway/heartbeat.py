"""每日心跳上报（agent-product-design §8.3 规模期 + §10.1 Phase 4 批次B）。

一校一盒的远程可观测性：每盒每日一次**出站**心跳，向运营方（我方）汇报
版本 / readiness / 磁盘水位——「用户开口前知道谁挂了」。纪律与全产品一致：

- **出站-only**（§8.2）：网关主动 POST 到 SC_HEARTBEAT_URL，校内不开任何
  入站端口；URL 未配置 = 功能关闭，静默空转；
- **锦上添花**（§5.8）：采集或投递失败只记日志，绝不影响主流程；
- 数据最小化：只发运行指标（版本/探针/磁盘/进程面），不发任何业务数据。

载荷形状（heartbeat-v0.1.0）：
    {kind, box_id, version, ts, ready, backend_ready, disk: {…}, bridges, budget}

独立模块便于测试（不依赖 FastAPI 运行时）。
"""

from __future__ import annotations

import os
import shutil
import time

import httpx

HEARTBEAT_VERSION = "heartbeat-v0.1.0"
INTERVAL_S = float(os.environ.get("SC_HEARTBEAT_INTERVAL_S", "86400"))  # 每日
TIMEOUT_S = float(os.environ.get("SC_HEARTBEAT_TIMEOUT", "8"))

# 磁盘水位关注路径：CODEX_HOME 卷（rollout/线程记忆）与备份卷是唯二会涨的
_WATCH_PATHS = ("SC_GATEWAY_CODEX_HOME", "SC_BACKUP_DIR")


def _disk_usage() -> list[dict]:
    out = []
    for env_key in _WATCH_PATHS:
        path = os.environ.get(env_key, "")
        if not path:
            continue
        try:
            u = shutil.disk_usage(path)
            out.append({
                "path": path,
                "total_gb": round(u.total / 2**30, 2),
                "used_gb": round(u.used / 2**30, 2),
                "free_gb": round(u.free / 2**30, 2),
            })
        except OSError as e:
            print(f"[heartbeat] disk probe failed for {path}: {e}")
    return out


def collect_payload(
    *,
    box_id: str | None = None,
    version: str | None = None,
) -> dict:
    """采一帧心跳。探针失败按 False 上报（诚实降级），绝不抛异常。

    box_id 缺省取 SC_BOX_ID 或主机名；version 缺省取 SC_BOX_VERSION。
    """
    from gateway.main import backend_ready, health_snapshot  # 延迟导入避免环

    snap = health_snapshot()
    return {
        "kind": "box_heartbeat",
        "schema_version": HEARTBEAT_VERSION,
        "box_id": box_id or os.environ.get("SC_BOX_ID", "") or os.uname().nodename,
        "version": version or os.environ.get("SC_BOX_VERSION", ""),
        "ts": int(time.time()),
        "gateway_ok": True,
        "backend_ready": backend_ready(),
        "bridges": snap.get("bridges", {}),
        "budget_tasks": len(snap.get("budget_tasks", {}) or {}),
        "disk": _disk_usage(),
    }


def deliver(payload: dict, url: str, *, client: httpx.Client | None = None) -> bool:
    """POST 心跳到运营端点。返回是否成功；任何异常吞掉记 False。"""
    if not url:
        return False
    try:
        if client is not None:
            r = client.post(url, json=payload)
        else:
            with httpx.Client(timeout=TIMEOUT_S) as hc:
                r = hc.post(url, json=payload)
        ok = r.status_code < 300
        if not ok:
            print(f"[heartbeat] rejected: {r.status_code} {r.text[:200]}")
        return ok
    except Exception as e:  # noqa: BLE001 —— 触达失败不上抛（§5.8）
        print(f"[heartbeat] delivery failed: {e}")
        return False


async def heartbeat_loop(stop_flag: list) -> None:
    """每日心跳循环（main.py lifespan 启动；stop_flag[0]=True 退出）。

    SC_HEARTBEAT_URL 未配置时静默空转——单校试点默认关闭，规模期开启。
    """
    import asyncio

    url = os.environ.get("SC_HEARTBEAT_URL", "")
    while not stop_flag[0]:
        if url:
            payload = await asyncio.to_thread(collect_payload)
            await asyncio.to_thread(deliver, payload, url)
        waited = 0.0
        while waited < INTERVAL_S and not stop_flag[0]:
            await asyncio.sleep(min(5.0, INTERVAL_S - waited))
            waited += 5.0


def reset_for_tests() -> None:
    """测试间清理（当前无全局状态，占位保持与其他模块一致的接口）。"""
