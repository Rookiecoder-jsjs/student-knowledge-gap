"""装车批第 5 批：_child_env 最小权限白名单测试；装车批第 6 批：驱动 home 分离。

断言 agent 子进程 env 绝不含共享密钥/DB URL/LLM key（旧 `{**os.environ}` 全盘继承
的修复——注入的 agent 一次 `env` 即得签名密钥可伪造任意教师 token），只含 codex
运行所需 + 该教师身份 token + 该教师的驱动 CODEX_HOME（t<teacher_id>/，第 6 批）；
Bridge.spawn 实际把该白名单交给 Popen 且先按驱动 home 播种。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import gateway.main as gm

FORBIDDEN = (
    "SC_AUTH_SECRET",          # 签名密钥（agent 拿到可伪造任意教师 token）
    "SC_TRIGGER_KEY",          # /internal/* 共享密钥
    "SC_DATABASE_URL",         # sc.db 定位
    "SC_LLM_API_KEY",
    "SC_DEEPSEEK_API_KEY",
    "SC_GATEWAY_URL",
    "SC_GATEWAY_APP_SERVER",
    "SC_GATEWAY_APP_SERVER_ARGS",
    "SC_GATEWAY_CODEX_HOME",
    "SC_GATEWAY_ASSETS",
    "SC_DINGTALK_WEBHOOK",
    "SC_DINGTALK_SECRET",
    "SC_HEARTBEAT_URL",
    "SC_RETENTION_ROLLOUT_DAYS",
    "SC_BUDGET_MAX_TURNS",
    "SC_MCP_TEACHER_ID",
)


def _seed_secrets(monkeypatch) -> None:
    for k in FORBIDDEN:
        monkeypatch.setenv(k, f"leaked-{k}")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy:3128")
    monkeypatch.setenv("SC_GATEWAY_CODEX_HOME", "/data/codex-home")
    monkeypatch.setattr(gm, "CODEX_HOME", "/data/codex-home")
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", "s3cr3t")


# 跨平台期望值推导：Path("/data/codex-home") 在 Windows 会带盘符，不能用字面斜杠串。
def _home(tid: int) -> str:
    return str(Path("/data/codex-home") / f"t{tid}")


def test_child_env_excludes_all_sc_secrets(monkeypatch):
    _seed_secrets(monkeypatch)
    env = gm._child_env(0)
    for k in FORBIDDEN:
        assert k not in env, f"{k} 泄漏进子进程 env"
    # 第 6 批：驱动 home = 根下 t<teacher_id>/
    assert env["CODEX_HOME"] == _home(0)
    assert env["RUST_LOG"] == "error"
    assert env["HTTP_PROXY"] == "http://proxy:3128"


def test_child_env_adds_token_for_teacher(monkeypatch):
    _seed_secrets(monkeypatch)
    env = gm._child_env(7)
    assert env["CODEX_HOME"] == _home(7)
    assert env["SC_SCHOOL_AUTH_TOKEN"].split(".")[0] == "7"
    assert env["SC_SCHOOL_AUTH_TOKEN"].count(".") == 2


def test_child_env_no_token_without_secret(monkeypatch):
    _seed_secrets(monkeypatch)
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", "")
    env = gm._child_env(7)
    assert env["CODEX_HOME"] == _home(7)
    assert "SC_SCHOOL_AUTH_TOKEN" not in env
    assert not any(k.startswith("SC_") for k in env)


def test_bridge_spawn_passes_whitelist_env(monkeypatch):
    _seed_secrets(monkeypatch)
    captured: dict = {}

    class _Stream:
        def write(self, s):  # noqa: ANN001
            return len(s)

        def flush(self):
            return None

        def readline(self):  # noqa: ANN001
            return ""

    class _FakeProc:
        def __init__(self):
            self.stdin = _Stream()
            self.stdout = _Stream()

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw["env"]
        return _FakeProc()

    async def _noop_request(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return {}

    # 第 6 批：spawn 前会播种驱动 home——此处 stub，避免测试写真实 CODEX_HOME 根
    # （播种本身由 test_seed_driver_home_* 覆盖）。
    monkeypatch.setattr(
        gm, "_seed_driver_home", lambda teacher_id=0: Path(gm.CODEX_HOME) / f"t{teacher_id or 0}"
    )
    monkeypatch.setattr(gm.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(gm.Bridge, "request", _noop_request)

    asyncio.run(gm.Bridge.spawn(teacher_id=7))

    assert captured["cmd"] == [gm.APP_SERVER_CMD, *gm.APP_SERVER_ARGS]
    env = captured["env"]
    for k in FORBIDDEN:
        assert k not in env, f"{k} 经 Bridge.spawn 泄漏给 Popen"
    assert env["CODEX_HOME"] == _home(7)
    assert env["SC_SCHOOL_AUTH_TOKEN"].split(".")[0] == "7"


def test_driver_home_path(monkeypatch):
    """第 6 批：驱动 home = 根/t<teacher_id>；teacher 0 = 匿名 t0。"""
    monkeypatch.setattr(gm, "CODEX_HOME", "/data/codex-home")
    assert gm._driver_home(7) == Path("/data/codex-home") / "t7"
    assert gm._driver_home(0) == Path("/data/codex-home") / "t0"


def test_seed_driver_home_seeds_own_dir(monkeypatch, tmp_path):
    """第 6 批：Bridge.spawn 前播种只落本驱动 home（config.toml/models.json）。"""
    monkeypatch.setattr(gm, "CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr(
        gm, "_assets_dir",
        lambda: Path(__file__).resolve().parent / "assets" / "deepseek",
    )
    gm._seed_driver_home(7)
    cfg = tmp_path / "codex-home" / "t7" / "config.toml"
    assert cfg.exists()
    assert 'url = "http://backend:8000/mcp"' in cfg.read_text(encoding="utf-8")
    assert (tmp_path / "codex-home" / "t7" / "models.json").exists()
    assert not (tmp_path / "codex-home" / "t0").exists()  # 只播本驱动
    gm._seed_driver_home(7)  # 幂等：再次调用不炸、不覆盖既有配置
