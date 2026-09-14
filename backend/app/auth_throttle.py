"""小而可控的登录失败限流。

单节点默认使用进程内短期状态；配置 HA Redis 后，失败计数改为 Redis
原子计数，从而让多副本共享同一阈值。Redis 暂时不可用时降级到内存，
保证认证服务仍可用，但不把用户名、IP 或密码写入日志或存储键。
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

_lock = threading.Lock()
_failures: dict[str, deque[float]] = defaultdict(deque)
_redis_client = None
_redis_client_url: str | None = None
_redis_warning_logged = False
_logger = logging.getLogger("sc.auth.throttle")


def _digest(username: str, client_ip: str) -> str:
    material = f"{username.strip().casefold()}\0{client_ip.strip()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _redis():
    """Return a cached Redis client, or ``None`` when Redis is not configured."""
    global _redis_client, _redis_client_url
    url = redis_url()
    if not ha_enabled() or not url:
        return None
    with _lock:
        if _redis_client is not None and _redis_client_url == url:
            return _redis_client
        try:
            import redis

            _redis_client = redis.Redis.from_url(
                url, socket_connect_timeout=1, socket_timeout=1
            )
            _redis_client_url = url
            return _redis_client
        except Exception as exc:  # noqa: BLE001
            _warn_redis(exc)
            _redis_client = None
            _redis_client_url = None
            return None


def _warn_redis(exc: Exception) -> None:
    global _redis_warning_logged
    if not _redis_warning_logged:
        _logger.warning("redis login throttle unavailable; using local fallback: %s", exc)
        _redis_warning_logged = True


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
        try:
            count = int(client.get(f"sc:auth:login-fail:{key}") or 0)
            if count >= MAX_FAILURES:
                ttl = int(client.ttl(f"sc:auth:login-fail:{key}"))
                return max(1, min(ttl if ttl > 0 else WINDOW_SECONDS, WINDOW_SECONDS))
            return None
        except Exception as exc:  # noqa: BLE001
            _warn_redis(exc)
    return _memory_check(key, time.monotonic())


def record_failure(username: str, client_ip: str) -> None:
    key = _digest(username, client_ip)
    client = _redis()
    if client is not None:
        try:
            redis_key = f"sc:auth:login-fail:{key}"
            count = int(client.incr(redis_key))
            if count == 1:
                client.expire(redis_key, WINDOW_SECONDS)
            return
        except Exception as exc:  # noqa: BLE001
            _warn_redis(exc)
    _memory_failure(key, time.monotonic())


def record_success(username: str, client_ip: str) -> None:
    key = _digest(username, client_ip)
    client = _redis()
    if client is not None:
        try:
            client.delete(f"sc:auth:login-fail:{key}")
            return
        except Exception as exc:  # noqa: BLE001
            _warn_redis(exc)
    with _lock:
        _failures.pop(key, None)


def reset_for_tests() -> None:
    """清理本地状态；不触碰外部 Redis 数据。"""
    global _redis_client, _redis_client_url, _redis_warning_logged
    with _lock:
        _failures.clear()
        _redis_client = None
        _redis_client_url = None
        _redis_warning_logged = False
