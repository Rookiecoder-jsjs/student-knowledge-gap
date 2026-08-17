"""健康/就绪探针测试（基础设施化：/health liveness + /ready readiness）。

/ready 语义：DB 可达 = 200；LLM 熔断 = 仍 200 但 degraded=true（降级非宕机）；
DB 不可达 = 503（容器 healthcheck 据此触发重启自愈）。

关键实现约束：/ready **必须运行时动态读 app.db.engine**（而非 import 期绑定），
测试夹具会 monkeypatch app.db.engine 做引擎隔离——本文件用 client 夹具（替换
app.db.engine）验证该读法不破坏隔离。
"""

from __future__ import annotations

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
