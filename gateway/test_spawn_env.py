"""装车批第 5 批：_child_env 最小权限白名单测试。

断言 agent 子进程 env 绝不含共享密钥/DB URL/LLM key（旧 `{**os.environ}` 全盘继承
的修复——注入的 agent 一次 `env` 即得签名密钥可伪造任意教师 token），只含 codex
运行所需 + 该教师身份 token；Bridge.spawn 实际把该白名单交给 Popen。
"""

from __future__ import annotations

import asyncio

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


def test_child_env_excludes_all_sc_secrets(monkeypatch):
    _seed_secrets(monkeypatch)
    env = gm._child_env(0)
    for k in FORBIDDEN:
        assert k not in env, f"{k} 泄漏进子进程 env"
    assert env["CODEX_HOME"] == "/data/codex-home"
    assert env["RUST_LOG"] == "error"
    assert env["HTTP_PROXY"] == "http://proxy:3128"


def test_child_env_adds_token_for_teacher(monkeypatch):
    _seed_secrets(monkeypatch)
    env = gm._child_env(7)
    assert env["SC_SCHOOL_AUTH_TOKEN"].split(".")[0] == "7"
    assert env["SC_SCHOOL_AUTH_TOKEN"].count(".") == 2


def test_child_env_no_token_without_secret(monkeypatch):
    _seed_secrets(monkeypatch)
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", "")
    env = gm._child_env(7)
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

    monkeypatch.setattr(gm.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(gm.Bridge, "request", _noop_request)

    asyncio.run(gm.Bridge.spawn(teacher_id=7))

    assert captured["cmd"] == [gm.APP_SERVER_CMD, *gm.APP_SERVER_ARGS]
    env = captured["env"]
    for k in FORBIDDEN:
        assert k not in env, f"{k} 经 Bridge.spawn 泄漏给 Popen"
    assert env["SC_SCHOOL_AUTH_TOKEN"].split(".")[0] == "7"
