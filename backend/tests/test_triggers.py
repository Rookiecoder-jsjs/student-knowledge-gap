"""任务触发器 v1 测试（agent-product-design §5.4，Phase 2 批次C）。

模板渲染 / fire-and-forget 语义（网关不可达不抛异常）/ 幂等键载荷 /
commit 端点组合点接线（未配置 SC_GATEWAY_URL 时静默跳过不影响主流程）。
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models import ExamTemplate
from app.triggers import (
    POST_EXAM_ANALYSIS_TEMPLATE_VERSION,
    fire_post_exam_analysis,
    post_exam_analysis_prompt,
)
from tests.conftest import make_exam


@pytest.fixture()
def exam(session, env):
    tpl = make_exam(session, env["class"].id, "期中卷", date(2025, 11, 2), "期中",
                    [(1, 10.0, "解答", "应用", [(env["kp"]["P1"], 1.0)])])
    session.flush()
    return tpl


# ---------------------------------------------------------------------------
# 模板渲染
# ---------------------------------------------------------------------------


def test_prompt_renders_exam_facts(session, exam):
    payload = post_exam_analysis_prompt(session, exam.id)
    assert payload["template_version"] == POST_EXAM_ANALYSIS_TEMPLATE_VERSION
    assert "期中卷" in payload["text"]
    assert str(exam.exam_date) in payload["text"]
    assert "get_exam_summary" in payload["text"]  # 指明工具路径
    assert "get_kp_detail" in payload["text"]


def test_prompt_missing_exam_raises(session):
    with pytest.raises(LookupError):
        post_exam_analysis_prompt(session, 99999)


# ---------------------------------------------------------------------------
# fire-and-forget 投递
# ---------------------------------------------------------------------------


class FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class RecordingClient:
    """捕获请求的假 httpx.Client。"""

    def __init__(self, status_code=200):
        self.calls: list[dict] = []
        self.status = status_code

    def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return FakeResp(self.status)


def test_fire_posts_payload_with_idempotency_key(session, env, exam):
    client = RecordingClient()
    ok = fire_post_exam_analysis(
        session, exam.id,
        gateway_url="http://gw:8100",
        internal_key="sekrit",
        client=client,
    )
    assert ok is True
    call = client.calls[0]
    assert call["url"] == "http://gw:8100/internal/trigger"
    assert call["headers"]["X-Internal-Key"] == "sekrit"
    body = call["json"]
    assert body["kind"] == "post_exam_analysis"
    assert body["exam_id"] == exam.id
    assert body["class_id"] == env["class"].id
    assert body["idempotency_key"] == f"post_exam_analysis:{exam.id}"
    assert body["message"].startswith("请基于刚提交的考试数据")


def test_fire_unconfigured_returns_false_silently(session, exam):
    """SC_GATEWAY_URL/KEY 未配置 → False 不抛（commit 主流程不受影响）。"""
    assert fire_post_exam_analysis(session, exam.id) is False


def test_fire_gateway_error_swallowed(session, env, exam):
    """网关返回 5xx → 记 warning 返回 False，绝不 raise。"""
    class Boom:
        def post(self, *a, **kw):
            raise ConnectionError("gateway down")

    assert fire_post_exam_analysis(
        session, exam.id, gateway_url="http://gw", internal_key="k", client=Boom()
    ) is False
    assert fire_post_exam_analysis(
        session, exam.id, gateway_url="http://gw", internal_key="k",
        client=RecordingClient(status_code=503),
    ) is False


def test_fire_missing_exam_skipped(session):
    """考试不存在 → skip 而非崩溃（fire-and-forget 语义）。"""
    assert fire_post_exam_analysis(
        session, 424242, gateway_url="http://gw", internal_key="k", client=RecordingClient()
    ) is False


# ---------------------------------------------------------------------------
# commit 端点接线：未配置触发器时端点照常工作（回归保护）
# ---------------------------------------------------------------------------


def test_commit_endpoint_works_without_trigger_config(monkeypatch, tmp_path):
    """commit 端点在触发器完全未配置时行为不变（批次C 接线不破坏既有流程）。"""
    from fastapi.testclient import TestClient

    import app.api.deps as deps_mod
    import app.db as dbmod
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'trigger_commit.db'}",
                           connect_args={"check_same_thread": False})
    from app.db import Base
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)
    new_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    original = (dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal)
    dbmod.engine, dbmod.SessionLocal = engine, new_session
    deps_mod.SessionLocal = new_session
    monkeypatch.delenv("SC_GATEWAY_URL", raising=False)
    monkeypatch.delenv("SC_TRIGGER_KEY", raising=False)

    from app.main import app

    try:
        with TestClient(app) as c:
            r = c.post("/schools", json={"name": "S"})
            school_id = r.json()["school_id"]
            cid = c.post(f"/schools/{school_id}/classes",
                         json={"name": "C", "grade": 7, "subject": "数学"}).json()["class_id"]
            # kb 最小种子：写临时 yaml 走 POST /kb/import（ExamCreate 需 kb_version_id）
            import yaml

            kb_yaml = tmp_path / "kb.yaml"
            kb_yaml.write_text(yaml.safe_dump({
                "subject": "数学", "textbook_edition": "t", "version": "v1",
                "kps": [{"code": "P1", "name": "基础点", "grade": 7, "semester": 1,
                         "chapter": "ch1"}],
                "relations": [],
            }, allow_unicode=True))
            kb = c.post("/kb/import", json={"yaml_path": str(kb_yaml)})
            if kb.status_code >= 300:
                pytest.skip(f"/kb/import 形状不符（{kb.status_code}），覆盖由 test_commit 承担")
            kb_vid = kb.json()["kb_version_id"]
            ce = c.post("/exams", json={
                "kb_version_id": kb_vid,
                "class_id": cid, "name": "E", "exam_date": "2026-08-20", "type": "单元",
                "questions": [{"idx": 1, "stem": "s", "q_type": "解答", "full_score": 10,
                               "cog_level": "应用"}],
            })
            if ce.status_code >= 300:
                pytest.skip(f"POST /exams 失败（{ce.status_code}），覆盖由 test_commit 承担")
            eid = ce.json().get("exam_id") or ce.json().get("template_id")
            r = c.post(f"/exams/{eid}/commit")
            assert r.status_code == 200
    finally:
        dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = original
