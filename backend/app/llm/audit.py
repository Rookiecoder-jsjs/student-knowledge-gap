"""LLM 调用全程审计（rollout 思想：append-only 落库、事后可回放取证）。

设计（借鉴 openai/codex rollout 的「事件流只追加、可回放」）：
- 唯一埋点：``AuditedClient`` 包住 ``get_client()`` 返回的任何客户端，
  五个调用点（narrate / tagger / batch_parse / template_parse / response_parse）
  与未来新增调用点自动全覆盖，重试的每次尝试各成一行；
- append-only：llm_call_log 只增不改；失败也留痕（status='error'），熔断快速
  拒绝记 status='circuit_open'（未触达 provider，duration=0）；
- PII 最小化：默认只记 (system,user,image) 的内容哈希与长度，不存原文；
  SC_LLM_AUDIT_PAYLOAD=1 时额外存解析后响应 JSON（调试用，生产默认关）。
  输入原文任何情况下不落库（含学生姓名/答卷图片）；
- 尽力而为：写入走有界队列 + 单写线程，队列满即丢弃并计数（绝不阻塞
  主流程）；SC_LLM_AUDIT=0 可整体关闭。审计任何环节的异常都不影响业务。

任务归属：调用方经 ``audit_context(task, prompt_version)`` 设置上下文
（contextvars，兼容 FastAPI 线程池与 batch worker 线程），缺省 "unknown"。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import threading
import time
from contextvars import ContextVar

from app.models import LlmCallLog

_log = logging.getLogger("sc.llm.audit")

# 有界队列：溢出说明审计消费跟不上调用速率，丢行优于阻塞调用方。
_QUEUE_MAX = 10_000
_queue: queue.Queue[LlmCallLog] = queue.Queue(maxsize=_QUEUE_MAX)
_dropped = 0
_drop_lock = threading.Lock()
_threads: list[tuple[threading.Thread, threading.Event]] = []
_threads_lock = threading.Lock()

# (task, prompt_version) 上下文；未设置时落 ("unknown", "")。
_ctx: ContextVar[tuple[str, str]] = ContextVar("llm_audit_ctx", default=("unknown", ""))


class _CtxToken:
    def __init__(self, task: str, prompt_version: str):
        self._task = task
        self._pv = prompt_version

    def __enter__(self):
        self._tok = _ctx.set((self._task, self._pv))
        return self

    def __exit__(self, *exc):
        _ctx.reset(self._tok)
        return False


def audit_context(task: str, prompt_version: str = "") -> _CtxToken:
    """标记当前逻辑流的审计任务名与 prompt 版本（with 用法）。"""
    return _CtxToken(task, prompt_version)


def current_task() -> str:
    return _ctx.get()[0]


def audit_enabled() -> bool:
    return os.environ.get("SC_LLM_AUDIT", "").lower() not in ("0", "false", "no")


def audit_payload_enabled() -> bool:
    """SC_LLM_AUDIT_PAYLOAD=1 时额外存响应 JSON（输入原文永不落库）。"""
    return os.environ.get("SC_LLM_AUDIT_PAYLOAD", "").lower() in ("1", "true", "yes")


def input_digest(
    system: str, user: str, image_bytes: bytes | None
) -> tuple[str, int, bool]:
    """(sha256, 输入字符数, has_image)。与 client._idempotency_key 同源口径。"""
    h = hashlib.sha256()
    h.update(system.encode())
    h.update(b"|")
    h.update(user.encode())
    h.update(b"|")
    if image_bytes:
        h.update(image_bytes)
    return h.hexdigest(), len(system) + len(user), bool(image_bytes)


def record_call(
    *,
    capability: str,
    status: str,
    duration_ms: int,
    system: str,
    user: str,
    image_bytes: bytes | None,
    provider: str = "",
    model: str = "",
    error: str | None = None,
    response_json: dict | None = None,
) -> None:
    """构造审计行入队。永不抛错——审计失败不能影响业务。"""
    if not audit_enabled():
        return
    digest, chars, has_image = input_digest(system, user, image_bytes)
    task, prompt_version = _ctx.get()
    row = LlmCallLog(
        capability=capability,
        task=task,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        status=status,
        duration_ms=duration_ms,
        error=(str(error)[:2000] if error else None),
        input_sha256=digest,
        input_chars=chars,
        has_image=has_image,
        response_json=response_json if audit_payload_enabled() else None,
    )
    try:
        _queue.put_nowait(row)
    except queue.Full:
        global _dropped
        with _drop_lock:
            _dropped += 1


def record_circuit_open(capability: str, error: str) -> None:
    """熔断快速拒绝留痕（未触达 provider，duration=0，无输入摘要可记）。"""
    record_call(
        capability=capability,
        status="circuit_open",
        duration_ms=0,
        system="",
        user="",
        image_bytes=None,
        error=error,
    )


def start_audit_worker(session_factory) -> None:
    """启动单写线程（幂等：已有存活线程时不重复启动）。

    session_factory 通常为 app.db.SessionLocal；测试可注入内存库工厂。
    写线程为 daemon，``stop_audit_workers()`` 可停。
    """
    with _threads_lock:
        for t, _stop in _threads:
            if t.is_alive():
                return  # 已有存活的写线程
        stop = threading.Event()

        def _run():
            while not stop.is_set():
                try:
                    row = _queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if row is None:  # shutdown 哨兵
                    return
                _write_row(session_factory, row)

        t = threading.Thread(target=_run, name="llm-audit-writer", daemon=True)
        t.start()
        _threads.append((t, stop))


def stop_audit_workers() -> None:
    """停止全部写线程（测试隔离 / 优雅退出用）。队列中的行保留待 flush。"""
    with _threads_lock:
        threads = list(_threads)
        _threads.clear()
    for t, stop in threads:
        stop.set()
        t.join(timeout=2)


def flush_pending(session_factory) -> int:
    """测试/运维用：同步把当前队列全部落库，返回成功写入行数。"""
    n = 0
    while True:
        try:
            row = _queue.get_nowait()
        except queue.Empty:
            break
        if row is not None and _write_row(session_factory, row):
            n += 1
    return n


def dropped_count() -> int:
    with _drop_lock:
        return _dropped


def _reset_for_tests() -> None:
    global _dropped
    stop_audit_workers()
    with _drop_lock:
        _dropped = 0
    while True:
        try:
            _queue.get_nowait()
        except queue.Empty:
            break
    _worker_started = False


def _write_row(session_factory, row: LlmCallLog) -> bool:
    """独立短事务写一行。失败仅记日志（审计尽力而为）。"""
    try:
        factory = session_factory
        if factory is None:
            from app.db import SessionLocal  # 延迟导入避免循环依赖

            factory = SessionLocal
        session = factory()
    except Exception as e:  # noqa: BLE001 —— 工厂本身故障同样不影响主流程
        _log.warning("LLM 审计会话创建失败", extra={"error": str(e)})
        return False
    try:
        session.add(row)
        session.commit()
        return True
    except Exception as e:  # noqa: BLE001 —— 审计绝不影响主流程
        _log.warning("LLM 审计落库失败", extra={"error": str(e)})
        session.rollback()
        return False
    finally:
        session.close()


class AuditedClient:
    """透明包装 BaseClient：parse_json 前后各记一行审计，语义不变。

    - 成功 → status='success'；payload 开关开启时附响应 JSON（超限截断）；
    - 异常 → status='error' + 错误摘要，原样 re-raise（降级决策仍在调用方）；
    - circuit_open 不在此记——熔断拒绝发生在调用 client 之前，
      由 gateway / _call_llm_with_retry 在捕获 CircuitOpenError 处记录。
    """

    def __init__(self, inner, capability: str):
        self._inner = inner
        self._capability = capability
        self.model_version = getattr(inner, "model_version", "unknown")

    def parse_json(self, system: str, user: str, image_bytes: bytes | None) -> dict:
        t0 = time.monotonic()
        try:
            payload = self._inner.parse_json(system, user, image_bytes)
        except Exception as e:
            self._emit(
                status="error",
                duration_ms=int((time.monotonic() - t0) * 1000),
                system=system,
                user=user,
                image_bytes=image_bytes,
                error=e,
            )
            raise
        self._emit(
            status="success",
            duration_ms=int((time.monotonic() - t0) * 1000),
            system=system,
            user=user,
            image_bytes=image_bytes,
            response_json=response_summary(payload)
            if isinstance(payload, dict)
            else {"payload": payload},
        )
        return payload

    def _emit(self, **kw) -> None:
        try:
            record_call(
                capability=self._capability,
                provider=_provider_name(self._inner),
                model=str(getattr(self._inner, "model_version", "unknown")),
                **kw,
            )
        except Exception:  # noqa: BLE001 —— 审计构造失败不影响调用
            _log.debug("LLM 审计入队失败", exc_info=True)

    def __getattr__(self, name):
        # 下划线属性缺失时直接抛 AttributeError，避免 _inner 未就绪时递归
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)


def _provider_name(inner) -> str:
    name = os.environ.get("SC_LLM_PROVIDER", "mock").lower()
    if name == "mock":
        # 环境为 mock 但拿到真实客户端（多为测试注入 override）：类名更有溯源价值
        name = type(inner).__name__
    return name


def unwrap(client):
    """剥掉审计包装（batch_upload 的 isinstance(mock) 判定等鸭子类型场景用）。"""
    return client._inner if isinstance(client, AuditedClient) else client


def wrap_client(client, capability: str):
    """get_client 出口统一包装。Mock 客户端同样包装（测试可断言审计行为）。"""
    if isinstance(client, AuditedClient):
        return client
    return AuditedClient(client, capability)


def response_summary(payload: dict, limit: int = 4000) -> dict:
    """payload 模式下的体积护栏：超限截断（防极端大响应撑爆行存储）。"""
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= limit:
        return payload
    return {"_truncated": True, "_preview": text[:limit]}
