"""装车批第 5 批：/mcp 逐请求鉴权 + auth.mcp_context contextvar 优先级测试。

覆盖（相对旧 stdio shim 模型的有意收紧）：
- unit：``set_mcp_teacher_id`` 写入 → ``auth.mcp_context`` 读出该教师；contextvar
  优先于 SC_MCP_TEACHER_ID env（HTTP 逐请求 > stdio 兜底）；双无 → 匿名；
- gate：安全模式无 token POST /mcp → **401**（fail-closed 于连接层，比旧「连上再
  逐工具拒」更彻底）；带有效教师 token → 通过鉴权门（非 401/421）；开放模式无
  token → 匿名放行（非 401）；
- 挂载冒烟：带 token 走完整 initialize + notifications/initialized + tools/list →
  sc 的 9 工具在场（/mcp 挂载 + 逐请求鉴权端到端）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app import auth
from app.db import Base
from app.models import Class, KbVersion, School, Student, Teacher, TeacherClass


@pytest.fixture()
def adb():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    s = S()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _fresh_auth():
    auth.reset_mode_cache_for_tests()
    yield
    auth.reset_mode_cache_for_tests()


def _seed(adb):
    school = School(name="测试学校")
    adb.add(school)
    adb.flush()
    kb = KbVersion(subject="数学", textbook_edition="t", version="1")
    adb.add(kb)
    adb.flush()
    c1 = Class(school_id=school.id, name="甲班", grade=7, subject="数学")
    c2 = Class(school_id=school.id, name="乙班", grade=7, subject="数学")
    adb.add_all([c1, c2])
    adb.flush()
    for alias in ("学生A", "学生B"):
        adb.add(Student(school_id=school.id, class_id=c1.id, name_or_alias=alias))
        adb.add(Student(school_id=school.id, class_id=c2.id, name_or_alias=alias))
    return school, kb, c1, c2


def _teacher(adb, name: str, username: str, password: str, *, admin=False):
    import secrets

    salt = secrets.token_bytes(16)
    t = Teacher(
        school_id=1, name=name, username=username,
        salt=salt, password_hash=auth.hash_password(password, salt), admin=admin,
    )
    adb.add(t)
    adb.flush()
    return t


# ---------------------------------------------------------------------------
# unit：contextvar 身份
# ---------------------------------------------------------------------------


def test_contextvar_teacher_resolves_and_guards(adb):
    school, kb, c1, c2 = _seed(adb)
    jia = _teacher(adb, "甲老师", "jia", "pass123")
    adb.add(TeacherClass(teacher_id=jia.id, class_id=c1.id))
    adb.commit()

    auth.set_mcp_teacher_id(jia.id)
    try:
        ctx = auth.mcp_context(adb)
        assert ctx.teacher is not None and ctx.teacher.id == jia.id
        # 同一裁决：自己的班过、乙班拒
        auth.assert_class_access(adb, ctx, c1.id)
        with pytest.raises(auth.PermissionError_):
            auth.assert_class_access(adb, ctx, c2.id)
    finally:
        auth.set_mcp_teacher_id(None)


def test_contextvar_precedes_env(adb, monkeypatch):
    school, kb, c1, c2 = _seed(adb)
    jia = _teacher(adb, "甲老师", "jia", "pass123")
    yi = _teacher(adb, "乙老师", "yi", "pass123")
    monkeypatch.setenv("SC_MCP_TEACHER_ID", str(yi.id))

    auth.set_mcp_teacher_id(jia.id)
    try:
        assert auth.mcp_context(adb).teacher.id == jia.id  # contextvar 优先
    finally:
        auth.set_mcp_teacher_id(None)
    assert auth.mcp_context(adb).teacher.id == yi.id  # env 兜底路径


def test_no_identity_anonymous(adb):
    _seed(adb)
    assert auth.mcp_context(adb).teacher is None


# ---------------------------------------------------------------------------
# gate：/mcp 逐请求鉴权（TestClient 全链路）
# ---------------------------------------------------------------------------


def _app_client(tmp_path, *, with_teachers: bool):
    """tmp 文件库 + monkeypatch app engine → TestClient(app)。"""
    db_file = tmp_path / "mcp-http.db"
    eng = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False, autoflush=False)

    from app.api import deps as deps_mod
    from app import db as dbmod
    from app.main import app

    old = (dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal)
    dbmod.engine = eng
    dbmod.SessionLocal = S
    deps_mod.SessionLocal = S

    ids: dict = {}
    s = S()
    school, kb, c1, c2 = _seed(s)
    if with_teachers:
        jia = _teacher(s, "甲老师", "jia", "pass123")
        yi = _teacher(s, "乙老师", "yi", "pass123")
        s.add(TeacherClass(teacher_id=jia.id, class_id=c1.id))
        s.add(TeacherClass(teacher_id=yi.id, class_id=c2.id))
        ids["jia"] = jia.id
        ids["yi"] = yi.id
    ids["c1"] = c1.id
    ids["c2"] = c2.id
    s.commit()
    s.close()

    try:
        with TestClient(app) as client:
            yield client, S, ids
    finally:
        dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = old
        eng.dispose()


@pytest.fixture()
def sec_client(tmp_path):
    yield from _app_client(tmp_path, with_teachers=True)


@pytest.fixture()
def open_client(tmp_path):
    yield from _app_client(tmp_path, with_teachers=False)


def test_mcp_anonymous_denied_in_security_mode(sec_client):
    """安全模式：无 token 的 /mcp 在连接层即 401（fail-closed 收紧）。"""
    client, S, ids = sec_client
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "2025-06-18",
                                             "capabilities": {}}})
    assert r.status_code == 401
    # 白名单探针不受 /mcp 收紧影响
    assert client.get("/health").status_code == 200


def test_mcp_valid_token_passes_gate(sec_client):
    """带有效教师 token（网关同款 HMAC 签名，auth.issue_token）→ 通过鉴权门。"""
    client, S, ids = sec_client
    tok = auth.issue_token(ids["jia"])
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code not in (401, 421, 307)


def test_mcp_invalid_token_denied(sec_client):
    client, S, ids = sec_client
    r = client.post("/mcp", json={}, headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


def test_mcp_anonymous_allowed_in_open_mode(open_client):
    """开放模式（无凭据教师）：匿名 /mcp 放行到协议层。"""
    client, S, ids = open_client
    r = client.post("/mcp", json={})
    assert r.status_code != 401


def _sse_text(r) -> str:
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        data = ""
        for line in r.text.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
        return data
    return r.text


def test_mcp_handshake_lists_nine_tools(sec_client):
    """带 token 走完整 initialize → tools/list：sc 9 工具在场（挂载端到端）。"""
    import json

    client, S, ids = sec_client
    tok = auth.issue_token(ids["jia"])
    hdr = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
           "Accept": "application/json, text/event-stream"}
    r = client.post("/mcp", headers=hdr, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "1"}},
    })
    assert r.status_code == 200, r.text[:300]
    sid = r.headers.get("mcp-session-id")
    assert sid

    client.post("/mcp", headers={**hdr, "mcp-session-id": sid}, json={
        "jsonrpc": "2.0", "method": "notifications/initialized"})

    r2 = client.post("/mcp", headers={**hdr, "mcp-session-id": sid}, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert r2.status_code == 200, r2.text[:300]
    body = json.loads(_sse_text(r2))
    tools = body["result"]["tools"]
    names = {t["name"] for t in tools}
    expected = {
        "get_class_overview", "get_exam_summary", "get_kp_mastery",
        "run_attribution", "get_kp_detail", "get_teaching_progress",
        "list_students", "create_report_draft_tool", "record_intervention_tool",
    }
    assert names == expected
