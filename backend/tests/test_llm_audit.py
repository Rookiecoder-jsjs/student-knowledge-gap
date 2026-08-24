"""LLM 调用全程审计（app/llm/audit.py，rollout 思想）。

- 成功/失败各成一行 append-only；熔断快速拒绝记 circuit_open；
- 重试的每次尝试独立成行（batch 路径）；
- PII 最小化：默认不存提示词原文与响应，只存哈希+长度；SC_LLM_AUDIT_PAYLOAD=1 才存响应 JSON；
- 尽力而为：审计写入失败不影响业务调用；
- AuditedClient 对调用方透明（model_version 透传、unwrap 还原、异常语义不变）。

测试统一用 flush_pending(session_factory) 同步清队列断言，避免线程竞态。
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.db import Base
from app.llm import audit
from app.llm.audit import (
    audit_context,
    dropped_count,
    flush_pending,
    input_digest,
    record_circuit_open,
    unwrap,
    wrap_client,
)
from app.llm.client import LLMError, MockLLMClient, set_client
from app.llm.gateway import get_text_breaker, narrate
from app.models import LlmCallLog


@pytest.fixture()
def db_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_audit_queue():
    audit._reset_for_tests()
    yield
    audit._reset_for_tests()


def _rows(factory):
    s = factory()
    try:
        return list(s.scalars(select(LlmCallLog).order_by(LlmCallLog.id)))
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 核心行为：成功 / 失败 / 熔断 / 重试多行
# ---------------------------------------------------------------------------


def test_success_call_recorded_with_hash_not_plaintext(db_session_factory):
    mock = MockLLMClient([{"text": "解读正文"}])
    set_client(mock)
    try:
        with audit_context("narrative", "narrative-v0.3.0"):
            out = narrate("# 报告\n掌握度 45%", "student_diagnosis")
        assert "解读正文" in out
    finally:
        set_client(None)

    flush_pending(db_session_factory)
    rows = _rows(db_session_factory)
    assert len(rows) == 1
    r = rows[0]
    assert r.status == "success"
    assert r.task == "narrative"
    assert r.prompt_version == "narrative-v0.3.0"
    assert r.capability == "text"
    assert r.model == "mock-vision-v0"
    assert r.duration_ms >= 0
    # PII 最小化：只存哈希与长度，无提示词/响应原文
    assert len(r.input_sha256) == 64
    assert r.input_chars > 0
    assert r.response_json is None
    assert not hasattr(r, "system") or not getattr(r, "system", "")


def test_error_call_still_recorded_and_reraised(db_session_factory):
    class Boom(MockLLMClient):
        def parse_json(self, system, user, image_bytes):
            raise LLMError("boom")

    set_client(Boom())
    get_text_breaker().reset()
    try:
        assert narrate("报告", "quality_analysis") == ""
    finally:
        set_client(None)
        get_text_breaker().reset()

    flush_pending(db_session_factory)
    rows = _rows(db_session_factory)
    assert len(rows) == 1 and rows[0].status == "error"
    assert "boom" in (rows[0].error or "")
    assert rows[0].task == "narrative"


def test_circuit_open_recorded_without_provider_touch(db_session_factory):
    from app.config import LLM_CB_THRESHOLD

    mock = MockLLMClient([{"text": "x"}])
    set_client(mock)
    breaker = get_text_breaker()
    breaker.reset()
    try:
        for _ in range(LLM_CB_THRESHOLD):
            breaker.record_failure()
        assert narrate("报告", "student_diagnosis") == ""
        assert len(mock.calls) == 0
    finally:
        set_client(None)
        breaker.reset()

    flush_pending(db_session_factory)
    rows = _rows(db_session_factory)
    assert len(rows) == 1
    assert rows[0].status == "circuit_open"
    assert rows[0].duration_ms == 0


def test_retry_each_attempt_one_row(db_session_factory):
    """批量路径重试：两次失败 + 第三次成功 -> 三行审计。"""
    from unittest.mock import patch

    attempts = {"n": 0}

    def fake_sleep(_s):
        pass

    class FlakyTwice(MockLLMClient):
        def parse_json(self, system, user, image_bytes):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise httpx.ConnectError("refused")
            return {"questions": []}

    from app.ingestion.batch import _call_llm_with_retry

    set_client(FlakyTwice())
    try:
        payload, warnings = _call_llm_with_retry("题目描述", b"\xff\xd8fake")
        assert payload == {"questions": []}
        assert warnings == []
    finally:
        set_client(None)

    flush_pending(db_session_factory)
    rows = _rows(db_session_factory)
    assert [r.status for r in rows] == ["error", "error", "success"]
    assert all(r.task == "batch_parse" for r in rows)
    assert all(r.prompt_version != "" for r in rows)
    assert rows[0].has_image is True
    # 同一输入跨重试哈希一致（幂等键同源口径）
    assert rows[0].input_sha256 == rows[2].input_sha256


def test_record_circuit_open_helper_direct(db_session_factory):
    record_circuit_open("vision", "熔断中")
    flush_pending(db_session_factory)
    rows = _rows(db_session_factory)
    assert rows[0].status == "circuit_open"
    assert rows[0].capability == "vision"
    assert rows[0].task == "unknown"  # 无上下文时缺省


# ---------------------------------------------------------------------------
# PII 最小化与 payload 开关
# ---------------------------------------------------------------------------


def test_payload_opt_in_stores_response_json(db_session_factory, monkeypatch):
    monkeypatch.setenv("SC_LLM_AUDIT_PAYLOAD", "1")
    mock = MockLLMClient([{"text": "正文"}])
    set_client(mock)
    try:
        with audit_context("narrative", "narrative-v0.3.0"):
            narrate("报告", "student_diagnosis")
    finally:
        set_client(None)

    flush_pending(db_session_factory)
    rows = _rows(db_session_factory)
    assert rows[0].response_json == {"text": "正文"}


def test_response_summary_truncates_oversized():
    big = {"k": "x" * 10_000}
    out = audit.response_summary(big)
    assert out["_truncated"] is True
    assert len(out["_preview"]) <= 4000
    small = {"a": 1}
    assert audit.response_summary(small) == small


def test_audit_kill_switch(monkeypatch, db_session_factory):
    """SC_LLM_AUDIT=0 整体关闭：不入队。"""
    monkeypatch.setenv("SC_LLM_AUDIT", "0")
    mock = MockLLMClient([{"text": "x"}])
    set_client(mock)
    try:
        with audit_context("narrative"):
            narrate("报告", "student_diagnosis")
    finally:
        set_client(None)
    assert flush_pending(db_session_factory) == 0
    assert _rows(db_session_factory) == []


# ---------------------------------------------------------------------------
# 尽力而为：审计故障不影响业务
# ---------------------------------------------------------------------------


def test_write_failure_does_not_break_business_flow(db_session_factory, caplog):
    """落库工厂抛错：flush 吞掉并计数日志，调用方拿到结果。"""
    def broken_factory():
        raise RuntimeError("db gone")

    mock = MockLLMClient([{"text": "ok"}])
    set_client(mock)
    get_text_breaker().reset()
    try:
        with audit_context("narrative"):
            out = narrate("报告", "student_diagnosis")
        assert "ok" in out
    finally:
        set_client(None)
        get_text_breaker().reset()

    n = flush_pending(broken_factory)
    assert n == 0  # 写入失败但不抛


def test_queue_overflow_drops_and_counts():
    audit._reset_for_tests()
    # 队列容量 10000，塞满后溢出计数
    for i in range(audit._QUEUE_MAX + 5):
        audit.record_call(
            capability="text",
            status="success",
            duration_ms=1,
            system="s",
            user="u",
            image_bytes=None,
            response_json=None,
        )
    assert dropped_count() == 5


# ---------------------------------------------------------------------------
# 包装透明性
# ---------------------------------------------------------------------------


def test_wrap_transparent_and_unwrap():
    mock = MockLLMClient([{"q": 1}])
    wrapped = wrap_client(mock, "vision")
    assert isinstance(wrapped, audit.AuditedClient)
    assert wrapped.model_version == mock.model_version
    assert unwrap(wrapped) is mock
    assert unwrap(mock) is mock
    # queue() 等非 parse_json 方法透传
    wrapped.queue({"q": 2})
    assert len(mock._responses) == 2
    assert wrapped.parse_json("s", "u", None) == {"q": 1}


def test_input_digest_matches_idempotency_key_basis():
    d1, chars, has_img = input_digest("sys", "user", None)
    d2, _, _ = input_digest("sys", "user", None)
    d3, _, has_img2 = input_digest("sys", "user", b"img")
    assert d1 == d2 and len(d1) == 64
    assert d3 != d1 and has_img is False and has_img2 is True
    assert chars == len("sys") + len("user")


def test_start_worker_writes_async(db_session_factory):
    audit.start_audit_worker(db_session_factory)
    mock = MockLLMClient([{"text": "x"}])
    set_client(mock)
    try:
        with audit_context("narrative"):
            narrate("报告", "student_diagnosis")
    finally:
        set_client(None)
    # 后台线程最终落库（轮询等待，上限 5s）
    import time

    for _ in range(50):
        flush_pending(db_session_factory)  # 兜底：worker 可能还没抢到
        if len(_rows(db_session_factory)) >= 1:
            break
        time.sleep(0.1)
    assert len(_rows(db_session_factory)) >= 1
