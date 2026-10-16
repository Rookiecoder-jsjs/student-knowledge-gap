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
        self.loaded_threads: set[str] = set()

    async def request(self, method, params, timeout=180.0):
        self.calls.append((method, params))
        if method == "thread/start":
            # 真实形状：Bridge.request 已剥 JSON-RPC 信封，result 即 thread 对象。
            return {"thread": {"id": self.tid}}
        if method == "thread/resume":
            self.loaded_threads.add(params["threadId"])
        if method == "turn/start" and params["threadId"] not in self.loaded_threads:
            raise RuntimeError("thread not found in this app-server process")
        return {"ok": True}


@pytest.fixture()
def gw(monkeypatch, tmp_path):
    monkeypatch.setattr(gm, "INTERNAL_KEY", "test-key")
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", "")
    monkeypatch.setattr(gm, "THREADS_FILE", tmp_path / "threads.json")
    # 每测一个全新 GUARD：模块内多测共享 "th-1" 轮次计数 + test_budget 的
    # importlib.reload 会让 gm.GUARD 配置随 import 顺序漂移——隔离后稳定。
    import gateway.budget as _budget

    monkeypatch.setattr(gm, "GUARD", _budget.BudgetGuard())
    gm._RECENT_TRIGGERS.clear()
    gm._TRIGGER_INFLIGHT.clear()
    gm._TRIGGER_THREAD_LOCKS.clear()
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
    async def factory(teacher_id=0):  # 装车批第 5 批：trigger 桥按教师分
        return fake

    return factory


def test_trigger_creates_and_reuses_thread(gw, monkeypatch):
    c, tmp = gw
    fake = FakeBridge()
    monkeypatch.setattr(gm, "_trigger_bridge", _fake_factory(fake))

    r1 = _post(c, _body())
    assert r1.status_code == 200 and r1.json()["accepted"] is True
    assert r1.json()["thread_id"] == "th-1"
    # 线程映射已落盘（第 6 批：键 = class_id.teacher_id；缺省教师 → 0）
    saved = json.loads((tmp / "threads.json").read_text())
    assert saved == {"3.0": "th-1"}
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
    assert dup.json()["accepted"] is True
    assert dup.json()["duplicate"] is True
    assert dup.json()["thread_id"] == "th-1"


def test_trigger_unknown_kind_400(gw, monkeypatch):
    c, _ = gw
    r = _post(c, _body(kind="weekly_digest"))
    assert r.status_code == 400


def test_trigger_forwards_teacher_id_to_bridge(gw, monkeypatch):
    """装车批第 5 批：载荷 teacher_id → 按该身份取 trigger 桥（安全模式实名驱动）。"""
    c, _ = gw
    seen: dict = {}

    async def factory(teacher_id=0):
        seen["teacher_id"] = teacher_id
        return FakeBridge()

    monkeypatch.setattr(gm, "_trigger_bridge", factory)
    r = _post(c, _body(teacher_id=5))
    assert r.status_code == 200 and r.json().get("accepted") is True, r.text
    assert seen["teacher_id"] == 5
    # 缺省 → 匿名（teacher_id=0）
    r2 = _post(c, _body(idempotency_key="post_exam_analysis:9", exam_id=9))
    assert r2.status_code == 200
    assert seen["teacher_id"] == 0


def test_thread_key_is_class_dot_teacher(gw, monkeypatch):
    """装车批第 6 批：同班不同教师各持自己的持久线程（键 class_id.teacher_id）。"""
    c, tmp = gw
    fake = FakeBridge()
    monkeypatch.setattr(gm, "_trigger_bridge", _fake_factory(fake))

    # 教师 5 首建 → 键 3.5
    _post(c, _body(idempotency_key="post_exam_analysis:k1", exam_id=1, teacher_id=5))
    saved = json.loads((tmp / "threads.json").read_text())
    assert saved == {"3.5": "th-1"}
    # 同教师再触发 → 复用（不重复 thread/start）
    gm._RECENT_TRIGGERS.clear()
    _post(c, _body(idempotency_key="post_exam_analysis:k2", exam_id=2, teacher_id=5))
    starts = [m for m, _ in fake.calls if m == "thread/start"]
    assert len(starts) == 1
    # 匿名（教师 0）触发同班 → 独立键 3.0（两教师不共享一类线程）
    gm._RECENT_TRIGGERS.clear()
    _post(c, _body(idempotency_key="post_exam_analysis:k3", exam_id=3))
    saved = json.loads((tmp / "threads.json").read_text())
    assert saved == {"3.5": "th-1", "3.0": "th-1"}
    starts = [m for m, _ in fake.calls if m == "thread/start"]
    assert len(starts) == 2


def test_recreated_bridge_resumes_persisted_thread_before_starting_turn(gw, monkeypatch):
    c, _ = gw
    original = FakeBridge()
    monkeypatch.setattr(gm, "_trigger_bridge", _fake_factory(original))
    assert _post(c, _body()).json()["accepted"]
    replacement = FakeBridge()
    monkeypatch.setattr(gm, "_trigger_bridge", _fake_factory(replacement))

    response = _post(c, _body(exam_id=8, idempotency_key="post_exam_analysis:8"))

    assert response.status_code == 200
    assert [method for method, _ in replacement.calls] == ["thread/resume", "turn/start"]
    assert replacement.calls[0][1]["threadId"] == "th-1"


def test_failed_turn_can_retry_same_idempotency_key(gw, monkeypatch):
    c, _ = gw

    class FailOnce(FakeBridge):
        failures = 1

        async def request(self, method, params, timeout=180.0):
            if method == "turn/start" and self.failures:
                self.failures -= 1
                raise RuntimeError("temporary failure")
            return await super().request(method, params, timeout)

    fake = FailOnce()
    monkeypatch.setattr(gm, "_trigger_bridge", _fake_factory(fake))
    assert _post(c, _body()).status_code == 502
    retry = _post(c, _body())
    assert retry.status_code == 200
    assert retry.json()["accepted"] is True
    assert not retry.json().get("duplicate")


def test_secure_trigger_requires_teacher_before_creating_bridge(gw, monkeypatch):
    c, _ = gw
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", "configured-secret")
    fake = FakeBridge()
    monkeypatch.setattr(gm, "_trigger_bridge", _fake_factory(fake))
    assert _post(c, _body()).status_code == 400
    assert not fake.calls
    assert _post(c, _body(teacher_id=5)).json()["accepted"] is True


def test_concurrent_same_key_starts_one_turn(gw, monkeypatch):
    import asyncio

    fake = FakeBridge()
    factories = []

    async def slow_bridge(teacher_id=0):
        factories.append(teacher_id)
        await asyncio.sleep(0.01)
        return fake

    monkeypatch.setattr(gm, "_trigger_bridge", slow_bridge)

    async def scenario():
        request = gm.TriggerReq(**_body())
        return await asyncio.gather(gm.internal_trigger(request), gm.internal_trigger(request))

    first, second = asyncio.run(scenario())
    assert first["accepted"] and second["accepted"]
    assert second["duplicate"] is True
    assert len(factories) == 1
    assert [m for m, _ in fake.calls].count("turn/start") == 1
