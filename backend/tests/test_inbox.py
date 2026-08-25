"""收件箱与 draft 流测试（agent-product-design §5.3，Phase 2 批次B）。

状态机迁移合法性 / 打回必附理由 / 列表过滤与预览 / 存量默认 issued 语义 /
端点级签发与打回（TestClient 走真实路由）。
"""

from __future__ import annotations

import pytest

from app import inbox
from app.models import Report


def _report(session, **kw) -> Report:
    """session 可为 Session 或 sessionmaker（端点测试传 factory，自动开会话）。"""
    if hasattr(session, "add"):
        s = session
        close = False
    else:
        s = session()
        close = True
    defaults = dict(type="class_improvement_advice", class_id=None,
                    content_markdown="# 建议\n\n- 杠杆一：巩固 P1", snapshot_json={})
    r = Report(**{**defaults, **kw})
    s.add(r)
    s.flush()
    rid = r.id
    if close:
        s.commit()  # 端点请求走独立会话，必须先落库可见
        s.close()
    return r


# ---------------------------------------------------------------------------
# 领域层：状态机
# ---------------------------------------------------------------------------


def test_new_report_defaults_issued(session):
    """存量/确定性报告默认 issued——「待签发」只属于 Agent 起草的 draft。"""
    r = _report(session)
    assert r.status == "issued"


def test_draft_issue_flow(session):
    r = _report(session, status="draft")
    out = inbox.transition(session, r.id, "issue")
    assert out["status"] == "issued"
    assert out["status_changed_at"]
    assert inbox.inbox_summary(session)["draft"] == 0


def test_draft_reject_requires_note(session):
    r = _report(session, status="draft")
    with pytest.raises(ValueError, match="理由"):
        inbox.transition(session, r.id, "reject")
    with pytest.raises(ValueError):
        inbox.transition(session, r.id, "reject", note="   ")
    out = inbox.transition(session, r.id, "reject", note="数字与证据对不上")
    assert out["status"] == "archived"
    assert out["status_note"] == "数字与证据对不上"


def test_terminal_states_locked(session):
    issued = _report(session)  # 默认 issued
    with pytest.raises(ValueError, match="不能执行"):
        inbox.transition(session, issued.id, "issue")
    archived = _report(session, status="draft")
    inbox.transition(session, archived.id, "archive")
    with pytest.raises(ValueError):
        inbox.transition(session, archived.id, "issue")


def test_unknown_action_and_missing_report(session):
    r = _report(session, status="draft")
    with pytest.raises(ValueError, match="未知操作"):
        inbox.transition(session, r.id, "delete")
    with pytest.raises(LookupError):
        inbox.transition(session, 99999, "issue")


# ---------------------------------------------------------------------------
# 列表与预览
# ---------------------------------------------------------------------------


def test_list_drafts_filter_and_preview(session):
    d1 = _report(session, status="draft",
                 content_markdown="# 很长的建议" + "x" * 300)
    _report(session)  # issued 不出现在 draft 列表
    data = inbox.list_drafts(session)
    assert data["total"] == 1
    item = data["items"][0]
    assert item["report_id"] == d1.id
    assert len(item["preview"]) == 200  # 截断预览
    assert item["chars"] > 200
    assert item["type_label"] == "班级改进意见"

    # 班级过滤
    _report(session, status="draft", class_id=77)
    assert inbox.list_drafts(session, class_id=77)["total"] == 1
    assert inbox.list_drafts(session)["total"] == 2

    # 非法状态参数
    with pytest.raises(ValueError, match="非法状态"):
        inbox.list_drafts(session, status="bogus")


def test_list_archived_keeps_content(session):
    """打回进 archived 后原文保留、可按状态回看。"""
    r = _report(session, status="draft")
    inbox.transition(session, r.id, "reject", note="重写")
    got = inbox.list_drafts(session, status="archived")
    assert got["total"] == 1
    full = inbox.get_report_checked(session, r.id)
    assert full.content_markdown.startswith("# 建议")


# ---------------------------------------------------------------------------
# 端点层（TestClient，临时文件库隔离——同 test_api_queries 夹具模式）
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    import app.api.deps as deps_mod
    import app.db as dbmod
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'inbox_test.db'}",
                           connect_args={"check_same_thread": False})
    from app.db import Base
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)
    new_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    original = (dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal)
    dbmod.engine, dbmod.SessionLocal = engine, new_session
    deps_mod.SessionLocal = new_session
    with TestClient(app_import()) as c:
        yield c, new_session
    dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = original


def app_import():
    from app.main import app

    return app


def test_endpoints_inbox_issue_reject(client):
    c, session = client
    d = _report(session, status="draft")

    # 列表 + 角标
    assert c.get("/inbox").json()["total"] == 1
    assert c.get("/inbox/summary").json()["draft"] == 1

    # 全文视图
    full = c.get(f"/reports/{d.id}/full").json()
    assert full["type_label"] == "班级改进意见"
    assert full["markdown"].startswith("#")

    # 签发
    r = c.post(f"/reports/{d.id}/issue", json={"note": None})
    assert r.status_code == 200 and r.json()["status"] == "issued"
    assert c.get("/inbox/summary").json()["draft"] == 0

    # 已签发再打回 → 400（终态锁定）
    assert c.post(f"/reports/{d.id}/reject", json={"note": "x"}).status_code == 400


def test_endpoint_reject_without_note_400(client):
    c, session = client
    d = _report(session, status="draft")
    assert c.post(f"/reports/{d.id}/reject", json={"note": ""}).status_code == 400


def test_endpoint_404(client):
    c, _ = client
    assert c.post("/reports/99999/issue", json=None).status_code == 404
