"""心跳上报测试（Phase 4 批次B，§8.3）。

未配置=静默空转；载荷形状与数据最小化；投递失败不上抛；
探针失败诚实降级为 False。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _reload(monkeypatch, **env):
    for k in ("SC_HEARTBEAT_URL", "SC_BOX_ID", "SC_BOX_VERSION",
              "SC_GATEWAY_CODEX_HOME", "SC_BACKUP_DIR", "SC_BACKEND_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        if v is not None:
            monkeypatch.setenv(k, v)
    from gateway import heartbeat

    return importlib.reload(heartbeat)


def test_deliver_disabled_without_url(monkeypatch):
    hb = _reload(monkeypatch)
    assert hb.deliver({"kind": "box_heartbeat"}, "") is False


def test_payload_shape_and_minimization(monkeypatch):
    """载荷只含运行指标——绝无业务数据字段（学生/成绩/对话内容）。"""
    hb = _reload(
        monkeypatch,
        SC_BOX_ID="box-pilot-01",
        SC_BOX_VERSION="0.3.0",
        SC_GATEWAY_CODEX_HOME="/tmp",
    )
    payload = hb.collect_payload()
    assert payload["kind"] == "box_heartbeat"
    assert payload["schema_version"] == hb.HEARTBEAT_VERSION
    assert payload["box_id"] == "box-pilot-01"
    assert payload["version"] == "0.3.0"
    assert isinstance(payload["backend_ready"], bool)
    assert isinstance(payload["bridges"], dict)
    assert isinstance(payload["disk"], list) and len(payload["disk"]) == 1
    d = payload["disk"][0]
    assert {"path", "total_gb", "used_gb", "free_gb"} <= set(d)
    # 数据最小化守卫：顶层键白名单
    allowed = {"kind", "schema_version", "box_id", "version", "ts",
               "gateway_ok", "backend_ready", "bridges", "budget_tasks", "disk"}
    assert set(payload) <= allowed


def test_deliver_success_and_rejection(monkeypatch):
    hb = _reload(monkeypatch)
    got = []

    class R:
        status_code = 200

        def json(self):
            return {}

    def fake_post(url, json=None, **kw):
        got.append((url, json))
        return R()

    client = httpx.Client()
    monkeypatch.setattr(client, "post", fake_post)
    assert hb.deliver({"kind": "x"}, "https://ops.example/hb", client=client) is True
    assert got[0][1]["kind"] == "x"

    class Bad(R):
        status_code = 500

    monkeypatch.setattr(client, "post", lambda url, json=None, **kw: Bad())
    assert hb.deliver({"kind": "x"}, "https://ops.example/hb", client=client) is False


def test_deliver_swallows_network_error(monkeypatch):
    hb = _reload(monkeypatch)
    with httpx.Client() as c:
        def boom(url, json=None, **kw):
            raise ConnectionError("断网")

        monkeypatch.setattr(c, "post", boom)
        assert hb.deliver({"k": 1}, "https://unreachable.invalid/hb", client=c) is False


def test_backend_ready_false_when_unconfigured(monkeypatch):
    hb = _reload(monkeypatch)
    from gateway.main import backend_ready

    assert backend_ready() is False
