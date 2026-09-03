"""保留期限策略（agent-product-design §9 合规表「保留期限」行；Phase 4 批次C）。

§9 定案：当学期热数据 + 历史学期归档压缩 + N 学年清理，管理员可设。
本模块负责壳侧 rollout 文件的清理半边（备份侧的压缩归档在
backend/scripts/backup_loop.sh）：

- 扫 ``$SC_GATEWAY_CODEX_HOME`` 下各驱动 home 的 ``sessions``/``archived_sessions``
  （装车批第 6 批起为 ``t*/sessions`` 等，另保留根级旧布局兜底）中
  ``rollout-*.jsonl``，mtime 早于 ``SC_RETENTION_ROLLOUT_DAYS`` 的删除；
- **默认 0 = 永不删除**：一班一线程的持久记忆就落在 rollout 里（§5.6），
  清理超期 = 该班记忆归零、线程下次重开。这是合规选项而非默认行为，
  学校在知情下配置（例如按学年清一次）；
- 只动匹配 ``rollout-*.jsonl`` 的文件——state_db 与线程映射不动，
  失效线程由壳侧报错自愈（教师端表现为该班重新开始对话）；
- 循环纪律与 monthly_usage/heartbeat 一致：每日一跑、异常只记日志、
  未配置路径静默空转。

独立模块便于测试（不依赖 FastAPI 运行时）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

INTERVAL_S = float(os.environ.get("SC_RETENTION_CHECK_S", "86400"))  # 每日


def rollout_max_age_days() -> int:
    """保留天数；0 = 功能关闭（默认，保护一班一线程持久记忆）。"""
    try:
        return max(0, int(os.environ.get("SC_RETENTION_ROLLOUT_DAYS", "0")))
    except ValueError:
        return 0


def _candidate_dirs(codex_home: Path) -> list[Path]:
    """被扫目录：根级 sessions/archived_sessions（第 6 批前旧布局兜底）+ 每个
    按教师驱动的 t*/sessions 与 t*/archived_sessions（第 6 批起 home 按教师分）。
    """
    dirs = [
        codex_home / "sessions",
        codex_home / "archived_sessions",
    ]
    for driver in codex_home.glob("t*"):
        if driver.is_dir():
            dirs.append(driver / "sessions")
            dirs.append(driver / "archived_sessions")
    return dirs


def clean_rollouts(
    codex_home: str | Path,
    max_age_days: int,
    *,
    now: float | None = None,
) -> dict:
    """删除超龄 rollout 文件。返回 {scanned, deleted, freed_bytes}。

    now 参数供测试注入时钟；任何单文件失败跳过继续（下一轮再试）。
    """
    if max_age_days <= 0:
        return {"scanned": 0, "deleted": 0, "freed_bytes": 0}
    root = Path(codex_home)
    if not root.is_dir():
        return {"scanned": 0, "deleted": 0, "freed_bytes": 0}
    cutoff = (now if now is not None else time.time()) - max_age_days * 86400
    scanned = deleted = freed = 0
    for base in _candidate_dirs(root):
        if not base.is_dir():
            continue
        for p in base.rglob("rollout-*.jsonl"):
            scanned += 1
            try:
                st = p.stat()
                if st.st_mtime < cutoff:
                    size = st.st_size
                    p.unlink()
                    deleted += 1
                    freed += size
            except OSError as e:
                print(f"[retention] skip {p}: {e}")
    return {"scanned": scanned, "deleted": deleted, "freed_bytes": freed}


async def retention_loop(stop_flag: list) -> None:
    """每日清理循环（main.py lifespan 启动；stop_flag[0]=True 退出）。"""
    import asyncio

    while not stop_flag[0]:
        home = os.environ.get("SC_GATEWAY_CODEX_HOME", "")
        days = rollout_max_age_days()
        if home and days:
            try:
                r = await asyncio.to_thread(clean_rollouts, home, days)
                if r["deleted"]:
                    print(f"[retention] removed {r['deleted']} rollouts, "
                          f"freed {r['freed_bytes'] / 2**20:.1f} MiB")
            except Exception as e:  # noqa: BLE001 —— 清理失败不影响主流程
                print(f"[retention] sweep failed: {e}")
        waited = 0.0
        while waited < INTERVAL_S and not stop_flag[0]:
            await asyncio.sleep(min(5.0, INTERVAL_S - waited))
            waited += 5.0
