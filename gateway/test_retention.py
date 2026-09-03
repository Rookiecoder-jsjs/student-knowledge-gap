"""保留期限测试（Phase 4 批次C，§9）。

rollout 清理的默认关闭（保护一班一线程记忆）、天龄判定、目录遍历、
单文件失败跳过；backup_loop.sh 的压缩归档用 shell 语义等价的独立验证。
"""

from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _reload(monkeypatch, **env):
    monkeypatch.delenv("SC_RETENTION_ROLLOUT_DAYS", raising=False)
    for k, v in env.items():
        if v is not None:
            monkeypatch.setenv(k, v)
    from gateway import retention

    return importlib.reload(retention)


def _mk_rollout(base: Path, day_offset: int) -> Path:
    """在 sessions/YYYY/MM/DD 下造一个 mtime=day_offset 天前的 rollout 文件。"""
    t = time.time() - day_offset * 86400
    lt = time.localtime(t)
    d = base / "sessions" / f"{lt.tm_year:04d}" / f"{lt.tm_mon:02d}" / f"{lt.tm_mday:02d}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"rollout-2026-01-01T00-00-00-test{day_offset}.jsonl"
    p.write_text("{}\n")
    os.utime(p, (t, t))
    return p


def test_default_disabled(monkeypatch):
    r = _reload(monkeypatch)
    assert r.rollout_max_age_days() == 0
    # 关闭时 clean_rollouts 是 no-op
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        _mk_rollout(Path(td), 400)
        assert r.clean_rollouts(td, 0)["deleted"] == 0


def test_deletes_only_stale_rollouts(monkeypatch):
    r = _reload(monkeypatch)
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        old = _mk_rollout(Path(td), 100)
        fresh = _mk_rollout(Path(td), 3)
        other = Path(td) / "sessions" / "state.db"
        other.write_bytes(b"x")
        res = r.clean_rollouts(td, 30)
        assert res["deleted"] == 1 and res["freed_bytes"] > 0
        assert not old.exists()
        assert fresh.exists()
        assert other.exists()


def test_covers_archived_sessions_and_missing_dir(monkeypatch):
    r = _reload(monkeypatch)
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "archived_sessions" / "2025"
        d.mkdir(parents=True)
        old = d / "rollout-old.jsonl"
        old.write_text("{}")
        os.utime(old, (time.time() - 200 * 86400,) * 2)
        assert r.clean_rollouts(td, 90)["deleted"] == 1
        # 不存在的根目录安全 no-op
        assert r.clean_rollouts(Path(td) / "nope", 30)["scanned"] == 0


def test_covers_driver_home_t_dirs(monkeypatch):
    """装车批第 6 批：驱动 home 按教师分（t*/）后，rollout 扫各 t* 下的 sessions。"""
    r = _reload(monkeypatch)
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        stale7 = _mk_rollout(root / "t7", 120)   # 应清
        fresh8 = _mk_rollout(root / "t8", 3)     # 保留
        res = r.clean_rollouts(root, 30)
        assert res["deleted"] == 1
        assert not stale7.exists()
        assert fresh8.exists()


def test_bad_env_value_falls_back_to_zero(monkeypatch):
    r = _reload(monkeypatch, SC_RETENTION_ROLLOUT_DAYS="abc")
    assert r.rollout_max_age_days() == 0
