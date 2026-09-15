"""小而可控的登录失败限流。

单节点默认使用进程内短期状态；配置 HA Redis 后，失败计数改为 Redis
原子计数，从而让多副本共享同一阈值。Redis 暂时不可用时降级到内存，
保证认证服务仍可用，但不把用户名、IP 或密码写入日志或存储键。

Redis 故障处理（两层）：
- 操作失败即失效缓存客户端并打开 ``REDIS_RETRY_SECONDS`` 熔断，窗口内
  直接走内存——避免每个登录请求都付 1s socket 超时；
- 告警按熔断窗口限频（而不是只报一次），降级状态对运维持续可见。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import defaultdict, deque

from app.ha import enabled as ha_enabled
from app.ha import redis_url

MAX_FAILURES = 5
WINDOW_SECONDS = 5 * 60
MAX_KEYS = 10_000
# Redis 操作失败后的熔断窗口：窗口内不再发起任何 Redis 调用（登录路径对
# 时延敏感），期满放行一次探测性重试——成功则恢复，失败则重新打开窗口。
REDIS_RETRY_SECONDS = 30.0

_lock = threading.Lock()
_failures: dict[str, deque[float]] = defaultdict(deque)
_redis_client = None
_redis_client_url: str | None = None
_redis_down_until = 0.0
_redis_last_warn = 0.0
_logger = logging.getLogger("sc.auth.throttle")


def _digest(username: str, client_ip: str) -> str:
    material = f"{username.strip().casefold()}\0{client_ip.strip()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _new_client(url: str):
    """创建 Redis 客户端（独立函数便于测试替身注入）。"""
    import redis

    return redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)


def _redis():
    """Return a cached Redis client, or ``None`` when Redis is not usable.

    返回 ``None`` 的三种情况：未配置 HA、熔断窗口内（上次操作失败）、
    客户端创建失败。熔断窗口内零网络调用，登录请求不付额外时延。
    """
    global _redis_client, _redis_client_url, _redis_down_until
    url = redis_url()
    if not ha_enabled() or not url:
        return None
    if time.monotonic() < _redis_down_until:
        return None
    with _lock:
        if _redis_client is not None and _redis_client_url == url:
            return _redis_client
        try:
            _redis_client = _new_client(url)
            _redis_client_url = url
            return _redis_client
        except Exception as exc:  # noqa: BLE001
            _redis_client = None
            _redis_client_url = None
            _redis_down_until = time.monotonic() + REDIS_RETRY_SECONDS
            _warn_redis(exc)
            return None


def _warn_redis(exc: Exception) -> None:
    """Redis 故障告警：每个熔断窗口至多一条（降级持续可见，不一次性静音）。"""
    global _redis_last_warn
    now = time.monotonic()
    if now - _redis_last_warn >= REDIS_RETRY_SECONDS:
        _redis_last_warn = now
        _logger.warning("redis login throttle unavailable; using local fallback: %s", exc)


def _mark_redis_down(exc: Exception) -> None:
    """操作失败：失效缓存客户端并打开熔断窗口。

    此前坏客户端会被永久复用、每个操作都重新付超时且只告警一次——
    这里显式失效并限频告警，恢复依赖熔断窗口到期后的探测性重试。
    """
    global _redis_client, _redis_client_url, _redis_down_until
    with _lock:
        _redis_client = None
        _redis_client_url = None
        _redis_down_until = time.monotonic() + REDIS_RETRY_SECONDS
    _warn_redis(exc)


def _prune_memory(key: str, now: float) -> deque[float]:
    values = _failures[key]
    cutoff = now - WINDOW_SECONDS
    while values and values[0] <= cutoff:
        values.popleft()
    if not values:
        _failures.pop(key, None)
        return deque()
    return values


def _memory_check(key: str, now: float) -> int | None:
    with _lock:
        values = _prune_memory(key, now)
        if len(values) < MAX_FAILURES:
            return None
        return max(1, int(WINDOW_SECONDS - (now - values[0])))


def _memory_failure(key: str, now: float) -> None:
    with _lock:
        values = _prune_memory(key, now)
        if key not in _failures:
            values = _failures[key]
        values.append(now)
        if len(_failures) > MAX_KEYS:
            # 只在异常高基数攻击时做一次廉价清理，避免字典无限增长。
            cutoff = now - WINDOW_SECONDS
            for old_key, old_values in list(_failures.items()):
                if not old_values or old_values[-1] <= cutoff:
                    _failures.pop(old_key, None)
            while len(_failures) > MAX_KEYS:
                _failures.pop(next(iter(_failures)), None)


def check(username: str, client_ip: str) -> int | None:
    """返回应等待的秒数；未达到阈值返回 ``None``。"""
    key = _digest(username, client_ip)
    client = _redis()
    if client is not None:
        redis_key = f"sc:auth:login-fail:{key}"
        try:
            count = int(client.get(redis_key) or 0)
            if count >= MAX_FAILURES:
                ttl = int(client.ttl(redis_key))
                if ttl == -2:
                    # GET 与 TTL 之间键刚好过期：不要把不存在的键误判为
                    #「永久锁死」，让本次请求按无失败记录处理。
                    return None
                if ttl == -1:
                    # 键无 TTL（历史坏数据或 incr 与 expire 之间进程死亡的产物）：
                    # 重新武装过期，把「永久锁死」收敛为「最多再等一个窗口」。
                    ttl = WINDOW_SECONDS
                    if not client.expire(redis_key, ttl):
                        # 键可能在 TTL 与 EXPIRE 之间刚好消失；不要把
                        # 已不存在的失败计数变成一次新的误锁。
                        return None
                return max(1, min(ttl, WINDOW_SECONDS))
            return None
        except Exception as exc:  # noqa: BLE001
            _mark_redis_down(exc)
    return _memory_check(key, time.monotonic())


def record_failure(username: str, client_ip: str) -> None:
    key = _digest(username, client_ip)
    client = _redis()
    if client is not None:
        try:
            redis_key = f"sc:auth:login-fail:{key}"
            count = int(client.incr(redis_key))
            if count == 1:
                # 固定窗口从第一次失败开始计时；不能在每次失败时续期，
                # 否则低频失败会滑动累积，和内存降级路径语义不一致。
                client.expire(redis_key, WINDOW_SECONDS)
            else:
                # 第一次 incr 成功、expire 失败时可能留下无 TTL 的历史键。
                # 只修复明确的 -1，不触碰已有 TTL，也不把已消失的键重新锁住。
                ttl = int(client.ttl(redis_key))
                if ttl == -1:
                    client.expire(redis_key, WINDOW_SECONDS)
            return
        except Exception as exc:  # noqa: BLE001
            _mark_redis_down(exc)
    _memory_failure(key, time.monotonic())


def record_success(username: str, client_ip: str) -> None:
    key = _digest(username, client_ip)
    client = _redis()
    if client is not None:
        try:
            client.delete(f"sc:auth:login-fail:{key}")
            return
        except Exception as exc:  # noqa: BLE001
            _mark_redis_down(exc)
    with _lock:
        _failures.pop(key, None)


def reset_for_tests() -> None:
    """清理本地状态；不触碰外部 Redis 数据。"""
    global _redis_client, _redis_client_url, _redis_down_until, _redis_last_warn
    with _lock:
        _failures.clear()
        _redis_client = None
        _redis_client_url = None
        _redis_down_until = 0.0
        _redis_last_warn = 0.0
