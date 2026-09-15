"""登录限流回归：内存路径语义 + Redis 路径的 TTL 自愈 / 熔断 / 告警限频。"""

from __future__ import annotations

import logging

from app import auth_throttle


class _FakeRedis:
    """模拟 redis.Redis：incr/get/ttl/expire/delete 的最小行为 + 可注故障。"""

    def __init__(self):
        self.data: dict[str, int] = {}
        self.ttls: dict[str, int | None] = {}
        self.fail_on: set[str] = set()
        self.expire_calls: list[tuple[str, int]] = []

    def _fail(self, op: str) -> None:
        if op in self.fail_on:
            raise ConnectionError(f"forced failure: {op}")

    def incr(self, key: str) -> int:
        self._fail("incr")
        self.data[key] = self.data.get(key, 0) + 1
        self.ttls.setdefault(key, None)
        return self.data[key]

    def expire(self, key: str, seconds: int) -> bool:
        self._fail("expire")
        self.expire_calls.append((key, seconds))
        if key in self.data:
            self.ttls[key] = seconds
            return True
        return False

    def get(self, key: str):
        self._fail("get")
        value = self.data.get(key)
        return None if value is None else str(value)

    def ttl(self, key: str) -> int:
        self._fail("ttl")
        if key not in self.data:
            return -2
        return self.ttls.get(key) or -1

    def delete(self, key: str):
        self._fail("delete")
        self.data.pop(key, None)
        self.ttls.pop(key, None)
        return 1


def _enable_fake_redis(monkeypatch, fake: _FakeRedis):
    """HA env 打开 + 客户端创建替换为假客户端（绕过真实 socket）。"""
    monkeypatch.setenv("SC_HA_ENABLED", "1")
    monkeypatch.setenv("SC_REDIS_URL", "redis://localhost:6379/0")
    builds = {"n": 0}

    def fake_new_client(url):
        builds["n"] += 1
        return fake

    monkeypatch.setattr(auth_throttle, "_new_client", fake_new_client)
    return builds


def _redis_key(username: str, ip: str) -> str:
    return f"sc:auth:login-fail:{auth_throttle._digest(username, ip)}"


# ---------------------------------------------------------------------------
# 内存路径（原有语义不变）
# ---------------------------------------------------------------------------


def test_local_throttle_blocks_after_repeated_failures(monkeypatch):
    monkeypatch.delenv("SC_HA_ENABLED", raising=False)
    monkeypatch.delenv("SC_REDIS_URL", raising=False)
    auth_throttle.reset_for_tests()

    for _ in range(auth_throttle.MAX_FAILURES - 1):
        auth_throttle.record_failure("teacher", "127.0.0.1")
        assert auth_throttle.check("teacher", "127.0.0.1") is None
    auth_throttle.record_failure("teacher", "127.0.0.1")
    retry_after = auth_throttle.check("teacher", "127.0.0.1")
    assert retry_after is not None and retry_after > 0


def test_success_clears_local_throttle(monkeypatch):
    monkeypatch.delenv("SC_HA_ENABLED", raising=False)
    monkeypatch.delenv("SC_REDIS_URL", raising=False)
    auth_throttle.reset_for_tests()
    for _ in range(auth_throttle.MAX_FAILURES):
        auth_throttle.record_failure("teacher", "127.0.0.1")
    assert auth_throttle.check("teacher", "127.0.0.1") is not None

    auth_throttle.record_success("teacher", "127.0.0.1")
    assert auth_throttle.check("teacher", "127.0.0.1") is None


# ---------------------------------------------------------------------------
# Redis 路径：TTL 丢失自愈（旧实现会永久锁死该 (用户名, IP) 组合）
# ---------------------------------------------------------------------------


def test_redis_key_without_ttl_is_rearmed_not_locked_forever(monkeypatch):
    auth_throttle.reset_for_tests()
    fake = _FakeRedis()
    _enable_fake_redis(monkeypatch, fake)

    key = _redis_key("teacher", "127.0.0.1")
    # 模拟历史坏数据：失败已达阈值且键无 TTL（incr 与 expire 之间进程死亡的产物）
    fake.data[key] = auth_throttle.MAX_FAILURES
    fake.ttls[key] = None

    retry_after = auth_throttle.check("teacher", "127.0.0.1")
    assert retry_after is not None and retry_after <= auth_throttle.WINDOW_SECONDS
    assert fake.ttls[key] == auth_throttle.WINDOW_SECONDS


def test_record_failure_survives_expire_failure(monkeypatch):
    auth_throttle.reset_for_tests()
    fake = _FakeRedis()
    _enable_fake_redis(monkeypatch, fake)
    fake.fail_on.add("expire")  # incr 成功后 expire 连接失败（TTL 丢失的事故现场）

    auth_throttle.record_failure("teacher", "127.0.0.1")
    key = _redis_key("teacher", "127.0.0.1")
    assert fake.data[key] == 1

    # 故障恢复后下一次失败只修复丢失的 TTL，键不会永久无 TTL
    fake.fail_on.clear()
    auth_throttle._redis_down_until = 0.0  # 手动结束熔断窗口（等价 30s 后）
    auth_throttle.record_failure("teacher", "127.0.0.1")
    assert fake.data[key] == 2
    assert fake.ttls[key] == auth_throttle.WINDOW_SECONDS
    assert auth_throttle.check("teacher", "127.0.0.1") is None


def test_record_failure_preserves_existing_ttl(monkeypatch):
    """Redis 固定窗口：后续失败不能把已有 TTL 重置为完整窗口。"""
    auth_throttle.reset_for_tests()
    fake = _FakeRedis()
    _enable_fake_redis(monkeypatch, fake)

    auth_throttle.record_failure("teacher", "127.0.0.1")
    key = _redis_key("teacher", "127.0.0.1")
    fake.ttls[key] = 42
    auth_throttle._redis_down_until = 0.0
    before = len(fake.expire_calls)

    auth_throttle.record_failure("teacher", "127.0.0.1")

    assert fake.ttls[key] == 42
    assert len(fake.expire_calls) == before


def test_redis_missing_key_after_get_is_not_rearmed(monkeypatch):
    """Redis TTL=-2 表示键已消失，不能按无 TTL 键重新武装并误锁。"""
    auth_throttle.reset_for_tests()
    fake = _FakeRedis()
    _enable_fake_redis(monkeypatch, fake)
    key = _redis_key("teacher", "127.0.0.1")

    class ExpiredBetweenReads(_FakeRedis):
        def get(self, requested_key):
            assert requested_key == key
            return str(auth_throttle.MAX_FAILURES)

        def ttl(self, requested_key):
            assert requested_key == key
            return -2

    expired = ExpiredBetweenReads()
    monkeypatch.setattr(auth_throttle, "_redis", lambda: expired)

    assert auth_throttle.check("teacher", "127.0.0.1") is None
    assert expired.expire_calls == []


# ---------------------------------------------------------------------------
# 熔断：操作失败后窗口内零 Redis 调用（登录不付 socket 超时），期满探测重试
# ---------------------------------------------------------------------------


def test_redis_failure_opens_cooldown_window(monkeypatch):
    auth_throttle.reset_for_tests()
    fake = _FakeRedis()
    fake.fail_on.add("incr")
    builds = _enable_fake_redis(monkeypatch, fake)

    auth_throttle.record_failure("u", "ip")  # 失败 → 内存兜底 + 熔断打开
    assert builds["n"] == 1

    # 熔断窗口内：不再触碰 Redis（零网络时延），失败只记内存
    auth_throttle.record_failure("u", "ip")
    auth_throttle.record_failure("u", "ip")
    assert builds["n"] == 1
    assert fake.data == {}

    # 窗口期满：探测性重试成功，恢复 Redis 计数
    fake.fail_on.clear()
    auth_throttle._redis_down_until = 0.0
    auth_throttle.record_failure("u", "ip")
    assert builds["n"] == 2
    assert fake.data[_redis_key("u", "ip")] == 1


def test_redis_warning_rate_limited_per_cooldown_window(monkeypatch, caplog):
    auth_throttle.reset_for_tests()
    fake = _FakeRedis()
    fake.fail_on.add("get")
    _enable_fake_redis(monkeypatch, fake)

    with caplog.at_level(logging.WARNING, logger="sc.auth.throttle"):
        auth_throttle.check("u", "ip")  # 第一次失败 → 告警
        auth_throttle._redis_down_until = 0.0
        auth_throttle.check("u", "ip")  # 窗口内再次失败 → 不重复告警
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
