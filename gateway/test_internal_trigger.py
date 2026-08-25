"""gateway /internal/trigger 接口测试（§5.4，批次C）。

不 spawn 真 app-server：monkeypatch _trigger_bridge 返回假 Bridge（记录
thread/start、turn/start 调用并回放固定 result）。覆盖：
- 密钥鉴权（未配置=403；错 key=403；对 key 放行）；
- 线程映射落盘与复用（首建后第二次同班不再 thread/start）；
- 幂等 TTL 内重复键 accepted=False；
- kind 白名单。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import gateway.main as gm


class FakeBridge:
    def __init__(self, tid="th-1"):
        self.tid = tid
        self.calls: list[tuple[str, dict]] = []

    async def request(self, method, params, timeout=180.0):
        self.calls.append((method, params))
        if method == "thread/start":
            return {"result": {"thread": {"id": self.tid}}}
        return {"result": {"ok": True}}


@pytest.fixture()
def gw(monkeypatch, tmp_path):
    monkeypatch.setattr(gm, "INTERNAL_KEY", "test-key")
    monkeypatch.setattr(gm, "THREADS_FILE", tmp_path / "threads.json")
    gm._RECENT_TRIGGERS.clear()
    client = TestClient(gm.app)
    return client, tmp_path


def _post(c, body, key="test-key"):
    return c.post("/internal/trigger", json=body, headers={"X-Internal-Key": key})


def _body(**kw):
    base = {
        "kind": "post_exam_analysis",
        "exam_id": 7,
        "class_id": 3,
        "idempotency_key": "post_exam_analysis:7",
        "message": "请分析",
        "template_version": "post-exam-analysis-v0.1.0",
    }
    base.update(kw)
    return base


def test_internal_key_required(gw, monkeypatch):
    c, _ = gw
    fake = FakeBridge()
    monkeypatch.setattr(gm, "_trigger_bridge", _fake_factory(fake))
    assert _post(c, _body(), key="").status_code == 403
    assert _post(c, _body(), key="wrong").status_code == 403


def _fake_factory(fake):
    async def factory():
        return fake

    return factory


def test_trigger_creates_and_reuses_thread(gw, monkeypatch):
    c, tmp = gw
    fake = FakeBridge()
    monkeypatch.setattr(gm, "_trigger_bridge", _fake_factory(fake))

    r1 = _post(c, _body())
    assert r1.status_code == 200 and r1.json()["accepted"] is True
    assert r1.json()["thread_id"] == "th-1"
    # 线程映射已落盘
    saved = json.loads((tmp / "threads.json").read_text())
    assert saved == {"3": "th-1"}
    # 第二次同班触发不再 thread/start
    gm._RECENT_TRIGGERS.clear()
    r2 = _post(c, _body(idempotency_key="post_exam_analysis:8", exam_id=8))
    assert r2.json()["accepted"] is True
    starts = [m for m, _ in fake.calls if m == "thread/start"]
    assert len(starts) == 1
    turns = [p for m, p in fake.calls if m == "turn/start"]
    assert turns[-1]["input"][0]["text"] == "请分析"


def test_trigger_duplicate_within_ttl(gw, monkeypatch):
    c, _ = gw
    monkeypatch.setattr(gm, "_trigger_bridge", _fake_factory(FakeBridge()))
    assert _post(c, _body()).json()["accepted"] is True
    dup = _post(c, _body())
    assert dup.status_code == 200
    assert dup.json()["accepted"] is False
    assert "duplicate" in dup.json()["reason"]


def test_trigger_unknown_kind_400(gw, monkeypatch):
    c, _ = gw
    r = _post(c, _body(kind="weekly_digest"))
    assert r.status_code == 400
