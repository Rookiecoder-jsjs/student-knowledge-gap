"""LLM 熔断器（G5）：provider 持续不可用时快速失败，避免 3 个 worker 全耗在 120s 超时上。

状态机：closed（正常） -> 连续失败达阈值 -> open（冷却期内直接 fast-fail）
       -> 冷却到期 -> half_open（放行一次试探）-> 成功 closed / 失败 open。

线程安全（批量 worker 多线程共享）。保持 provider 无关：只观测成败，不耦合具体客户端。
"""

from __future__ import annotations

import threading
import time

from app.config import LLM_CB_COOLDOWN_SECONDS, LLM_CB_THRESHOLD


class CircuitOpenError(Exception):
    """熔断开启：本次调用被快速拒绝，未触达 provider。"""


class CircuitBreaker:
    """轻量熔断器（固定阈值 + 冷却 + 半开试探）。"""

    def __init__(
        self,
        threshold: int = LLM_CB_THRESHOLD,
        cooldown: float = LLM_CB_COOLDOWN_SECONDS,
        clock=time.monotonic,
    ):
        self._threshold = threshold
        self._cooldown = cooldown
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None  # monotonic 时刻；None=closed

    @property
    def state(self) -> str:
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self._cooldown:
            return "half_open"
        return "open"

    def before_call(self) -> None:
        """调用前检查：open 态直接拒绝。half_open 放行一次试探。"""
        with self._lock:
            state = self._state_locked()
            if state == "open":
                raise CircuitOpenError(
                    f"LLM 熔断中（连续失败 {self._failures} 次），{self._cooldown:.0f}s 后重试"
                )

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold and self._opened_at is None:
                self._opened_at = self._clock()

    def reset(self) -> None:
        """测试用：复位到 closed。"""
        with self._lock:
            self._failures = 0
            self._opened_at = None


# 全局单例：视觉解析（拍照录入）共享一个熔断器。文本报告叙述另需时再加一个。
_vision_breaker = CircuitBreaker()


def get_vision_breaker() -> CircuitBreaker:
    return _vision_breaker
