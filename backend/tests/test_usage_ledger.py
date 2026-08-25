"""用量台账 v1 测试（agent-product-design §5.9，Phase 2 批次D）。

token 两列迁移（模型默认 NULL）/ usage 提取（OpenAI 与 Anthropic 键名）/
审计透传 / 聚合口径（仅 success、NULL 记 0、按 task/日 分组）/ 端点。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.admin_usage import _month_range, usage_by_day_task, usage_summary_month
from app.llm.client import BaseClient
from app.models import LlmCallLog


# ---------------------------------------------------------------------------
# 聚合层
# ---------------------------------------------------------------------------


def _log(session, *, task="narrative", at, status="success", pt=None, ct=None):
    session.add(LlmCallLog(
        capability="text", task=task, status=status, duration_ms=10,
        input_sha256="x" * 64, input_chars=100, has_image=False,
        prompt_tokens=pt, completion_tokens=ct,
        at=at,
    ))
    session.flush()


def test_month_range_valid_and_invalid():
    first, nxt = _month_range("2026-08")
    assert (first.day, nxt.month) == (1, 9)
    dec = _month_range("2026-12")[1]
    assert dec == __import__("datetime").date(2027, 1, 1)
    with pytest.raises(ValueError):
        _month_range("bogus")


def test_usage_aggregation_groups_by_day_task(session):
    _log(session, at=datetime(2026, 8, 3, 10), task="narrative", pt=100, ct=50)
    _log(session, at=datetime(2026, 8, 3, 15), task="narrative", pt=200, ct=80)
    _log(session, at=datetime(2026, 8, 4, 9), task="tagger", pt=300, ct=20)
    # 非本月不计
    _log(session, at=datetime(2026, 7, 30), pt=999, ct=999)

    data = usage_by_day_task(session, "2026-08")
    assert len(data["days"]) == 2
    d3 = data["days"][0]
    assert d3["date"].startswith("2026-08-03")
    assert d3["by_task"]["narrative"] == {"calls": 2, "prompt_tokens": 300, "completion_tokens": 130}
    assert data["by_task"]["tagger"]["prompt_tokens"] == 300
    assert data["total"]["calls"] == 3
    assert data["total"]["prompt_tokens"] == 600


def test_usage_excludes_failed_and_counts_null_as_zero(session):
    _log(session, at=datetime(2026, 8, 5), status="error", pt=None, ct=None)
    _log(session, at=datetime(2026, 8, 5), status="circuit_open")
    _log(session, at=datetime(2026, 8, 5), status="success")  # mock 行：NULL token

    data = usage_summary_month(session, "2026-08")
    assert data["total"]["calls"] == 1  # 仅 success 计调用数
    assert data["total"]["prompt_tokens"] == 0


# ---------------------------------------------------------------------------
# usage 提取与审计透传
# ---------------------------------------------------------------------------


def test_extract_usage_openai_and_anthropic_keys():
    assert BaseClient._extract_usage({"usage": {"prompt_tokens": 11, "completion_tokens": 7}}) == \
        {"prompt_tokens": 11, "completion_tokens": 7}
    assert BaseClient._extract_usage({"usage": {"input_tokens": 12, "output_tokens": 9}}) == \
        {"prompt_tokens": 12, "completion_tokens": 9}
    assert BaseClient._extract_usage({"choices": []}) is None


def test_record_call_persists_usage_columns(session):
    """usage 两列在模型上可写（record_call 入队异步落库，落库路径由 audit worker 测覆盖）。"""
    from app.models import LlmCallLog as L

    r = L(capability="text", task="t", provider="p", model="m", prompt_version="v",
          status="success", duration_ms=1, input_sha256="a" * 64, input_chars=1,
          has_image=False, prompt_tokens=42, completion_tokens=17)
    session.add(r)
    session.flush()
    got = session.get(L, r.id)
    assert got.prompt_tokens == 42 and got.completion_tokens == 17


def test_audited_client_forwards_last_usage(session, monkeypatch):
    """AuditedClient.parse_json 成功路径把 inner.last_usage 传给 record_call。"""
    from app.llm import audit
    from app.llm.audit import AuditedClient

    captured = {}

    class FakeInner:
        model_version = "fake"

        last_usage = None

        def parse_json(self, system, user, image_bytes):
            self.last_usage = {"prompt_tokens": 5, "completion_tokens": 3}
            return {"ok": 1}

    def fake_record_call(**kw):
        captured.update(kw)

    monkeypatch.setattr(audit, "record_call", fake_record_call)
    c = AuditedClient(FakeInner(), "text")
    out = c.parse_json("s", "u", None)
    assert out == {"ok": 1}
    assert captured["usage"] == {"prompt_tokens": 5, "completion_tokens": 3}


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client(tmp_path):
    from fastapi.testclient import TestClient

    import app.api.deps as deps_mod
    import app.db as dbmod
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'usage.db'}",
                           connect_args={"check_same_thread": False})
    from app.db import Base
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)
    new_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    original = (dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal)
    dbmod.engine, dbmod.SessionLocal = engine, new_session
    deps_mod.SessionLocal = new_session
    from app.main import app

    with TestClient(app) as c:
        yield c
    dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = original


def test_admin_usage_endpoint(api_client):
    r = api_client.get("/admin/usage?month=2026-08")
    assert r.status_code == 200
    body = r.json()
    assert body["month"] == "2026-08"
    assert set(body.keys()) >= {"days", "by_task", "total"}
    s = api_client.get("/admin/usage/summary").json()
    assert "total" in s and "top_task" in s


def test_admin_usage_bad_month_400(api_client):
    assert api_client.get("/admin/usage?month=nope").status_code == 400
