"""健康/就绪探针测试（基础设施化：/health liveness + /ready readiness）。

/ready 语义：DB 可达 = 200；LLM 熔断 = 仍 200 但 degraded=true（降级非宕机）；
DB 不可达 = 503（容器 healthcheck 据此触发重启自愈）。

关键实现约束：/ready **必须运行时动态读 app.db.engine**（而非 import 期绑定），
测试夹具会 monkeypatch app.db.engine 做引擎隔离——本文件用 client 夹具（替换
app.db.engine）验证该读法不破坏隔离。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import create_engine

from app.config import LLM_CB_THRESHOLD
from app.llm.circuit import get_vision_breaker
from app.llm.gateway import get_text_breaker

# 复用 test_runtime_goals 的隔离夹具（替换 app.db.engine / SessionLocal）
from tests.test_runtime_goals import client  # noqa: F401


def _trip_vision_breaker() -> None:
    """连开熔断器到 open 态（阈值次 record_failure）。"""
    for _ in range(LLM_CB_THRESHOLD):
        get_vision_breaker().record_failure()


def test_health_liveness(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    UUID(resp.headers["X-Request-ID"])


def test_request_id_is_normalized_and_metrics_expose_duration(client):
    supplied = "123e4567-e89b-12d3-a456-426614174000"
    resp = client.get("/health", headers={"X-Request-ID": supplied.upper()})
    assert resp.headers["X-Request-ID"] == supplied

    invalid = client.get("/health", headers={"X-Request-ID": "not-a-uuid"})
    assert invalid.headers["X-Request-ID"] != "not-a-uuid"
    UUID(invalid.headers["X-Request-ID"])

    client.get("/ready")
    metrics = client.get("/metrics").text
    assert "sc_http_request_duration_ms_count" in metrics
    assert "sc_http_request_duration_ms_sum" in metrics
    assert 'route="/health"' in metrics
    assert "sc_dependency_ready" in metrics


def test_ready_ok_when_db_up(client):
    resp = client.get("/ready")
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["degraded"] is False
    assert body["llm"] == {"vision": "closed", "text": "closed"}


def test_ready_503_when_db_unreachable(client):
    import app.db as dbmod

    bad_engine = create_engine(
        "sqlite:////tmp/definitely_missing_dir_9f3a/sc.db",
        connect_args={"check_same_thread": False},
    )
    original = dbmod.engine
    dbmod.engine = bad_engine
    try:
        resp = client.get("/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "error"
        assert body["database"] == "error"
        assert body["degraded"] is True
    finally:
        dbmod.engine = original


def test_ready_degraded_when_llm_breaker_open(client):
    get_text_breaker().reset()  # 确定性：仅 vision 熔断
    _trip_vision_breaker()
    resp = client.get("/ready")
    body = resp.json()
    assert resp.status_code == 200  # LLM 降级 ≠ 宕机：仍就绪
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["degraded"] is True
    assert body["llm"]["vision"] == "open"
    assert body["llm"]["text"] == "closed"


def test_ready_rejects_sqlite_in_ha_mode(client, monkeypatch):
    monkeypatch.setenv("SC_HA_ENABLED", "1")
    resp = client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error_code"] == "database_unavailable"
    assert body["detail"] == "依赖不可用"
    assert "requires PostgreSQL" not in resp.text


def test_unhandled_exception_returns_safe_request_id_response(client):
    from app.main import app
    from fastapi.testclient import TestClient

    @app.get("/__test__/ops-boom")
    def _ops_boom():
        raise RuntimeError("internal secret should stay in logs")

    route = app.routes[-1]
    try:
        with TestClient(app, raise_server_exceptions=False) as safe_client:
            response = safe_client.get(
                "/__test__/ops-boom",
                headers={"X-Request-ID": "123e4567-e89b-12d3-a456-426614174000"},
            )
    finally:
        app.routes.remove(route)

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "123e4567-e89b-12d3-a456-426614174000"
    assert response.json() == {
        "detail": "服务内部错误",
        "error_code": "internal_error",
        "request_id": "123e4567-e89b-12d3-a456-426614174000",
    }
    assert "internal secret" not in response.text


def test_structured_formatter_redacts_sensitive_extra_fields():
    import logging

    from app.observability import JsonFormatter

    record = logging.LogRecord(
        "sc.test",
        logging.ERROR,
        __file__,
        1,
        "operation failed",
        (),
        None,
    )
    record.request_id = "request-1"
    record.password = "do-not-log"
    record.authorization = "Bearer do-not-log"
    rendered = JsonFormatter().format(record)
    assert "request-1" in rendered
    assert "do-not-log" not in rendered
