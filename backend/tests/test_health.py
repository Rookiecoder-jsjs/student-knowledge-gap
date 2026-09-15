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


# ---------------------------------------------------------------------------
# 可观测性回归（code-review 修复）：计数唯一性 / 低基数标签 / chunked 守卫 /
# /ready 日志脱敏与限频
# ---------------------------------------------------------------------------


def _counter_value(metrics_text: str, metric: str, labels: str) -> float:
    for line in metrics_text.splitlines():
        if line.startswith(f"{metric}{labels} "):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


def test_unhandled_exception_counts_5xx_error_once(client):
    """未处理 500 的 http_errors_total 只能 +1（异常处理器与 finally 不得重复计数）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    @app.get("/__test__/ops-boom-once")
    def _boom_once():
        raise RuntimeError("boom once")

    route = app.routes[-1]
    labels = '{error_code="internal_error",status_class="5xx"}'
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            before = _counter_value(c.get("/metrics").text, "sc_http_errors_total", labels)
            resp = c.get("/__test__/ops-boom-once")
            assert resp.status_code == 500
            after = _counter_value(c.get("/metrics").text, "sc_http_errors_total", labels)
    finally:
        app.routes.remove(route)
    assert after - before == 1


def test_unmatched_route_uses_low_cardinality_label(client):
    """404 / 前置拒绝路径无路由对象：指标与日志的 route 必须是固定占位符，非原始 URL。"""
    resp = client.get("/definitely-not-a-route-42")
    assert resp.status_code == 404
    metrics = client.get("/metrics").text
    assert 'route="unmatched"' in metrics
    assert 'route="/definitely-not-a-route-42"' not in metrics


def test_login_body_guard_rejects_chunked_transfer(client):
    """chunked 传输（无 Content-Length）同样被登录体积守卫拦截，防内存耗尽绕过。"""
    oversized = b"x" * (64 * 1024 + 1)
    resp = client.post(
        "/auth/login",
        content=oversized,
        headers={"Content-Type": "application/json", "Transfer-Encoding": "chunked"},
    )
    assert resp.status_code == 413
    assert resp.json()["error_code"] == "request_body_too_large"


def test_label_value_escapes_newline_and_quotes():
    """label 值必须转义 \\ \" \\n \\r——否则 %0A 可向 /metrics exposition 注入指标行。"""
    from app.metrics import _render_labels

    rendered = _render_labels((("path", '/a"b\\c\nd'),))
    assert rendered == '{path="/a\\"b\\\\c\\nd"}'


def test_ready_failure_log_redacts_secrets_and_rate_limits(client, caplog):
    """/ready 失败日志：异常文本先脱敏（DSN/凭据），同一故障签名限频一条。"""
    import logging

    import app.db as dbmod
    import app.main as main_mod

    class _ExplodingEngine:
        def connect(self):
            raise RuntimeError(
                "connection to postgres://admin:hunter2@db:5432/sc failed password=hunter2"
            )

    original = dbmod.engine
    dbmod.engine = _ExplodingEngine()
    main_mod._reset_ready_log_state_for_tests()
    try:
        with caplog.at_level(logging.WARNING, logger="sc.health"):
            assert client.get("/ready").status_code == 503
            assert client.get("/ready").status_code == 503
    finally:
        dbmod.engine = original
        main_mod._reset_ready_log_state_for_tests()
    failed = [r for r in caplog.records if r.getMessage() == "readiness dependency failed"]
    assert len(failed) == 1
    database_error = failed[0].__dict__["database_error"]
    assert "hunter2" not in database_error


def test_ready_recovery_is_logged_once(client, caplog):
    """依赖恢复要有一条 INFO（告警恢复需通知，只报不收敛是设计明令禁止的）。"""
    import logging

    import app.db as dbmod
    import app.main as main_mod
    from sqlalchemy import create_engine

    class _ExplodingEngine:
        def connect(self):
            raise RuntimeError("db down")

    original = dbmod.engine
    dbmod.engine = _ExplodingEngine()
    main_mod._reset_ready_log_state_for_tests()
    try:
        assert client.get("/ready").status_code == 503
        dbmod.engine = create_engine("sqlite:///:memory:")
        with caplog.at_level(logging.INFO, logger="sc.health"):
            assert client.get("/ready").status_code == 200
    finally:
        dbmod.engine = original
        main_mod._reset_ready_log_state_for_tests()
    recovered = [
        r for r in caplog.records if r.getMessage() == "readiness dependency recovered"
    ]
    assert len(recovered) == 1
    assert recovered[0].__dict__["error_code"] == "dependencies_recovered"
