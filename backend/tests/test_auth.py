"""G11 鉴权测试（agent-product-design §5.5，Phase 3 批次A）。

出口判据①「教师甲看不到教师乙的班」三层验证：
- 单元层：token 签发/校验、断言函数、模式判定；
- API 层：登录 → 甲可访问自己的班 → 访问乙的班 403 → 匿名 401；
- MCP 层：SC_MCP_TEACHER_ID 注入后工具层同一裁决（兜底路线）。
"""

from __future__ import annotations

import os

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
    salt = secrets_token_bytes()
    t = Teacher(
        school_id=1,
        name=name,
        username=username,
        salt=salt,
        password_hash=auth.hash_password(password, salt),
        admin=admin,
    )
    adb.add(t)
    adb.flush()
    return t


def secrets_token_bytes() -> bytes:
    import secrets

    return secrets.token_bytes(16)


# ---------------------------------------------------------------------------
# 单元层
# ---------------------------------------------------------------------------


def test_token_roundtrip(adb):
    _seed(adb)
    t = _teacher(adb, "李老师", "li", "pass123")
    tok = auth.issue_token(t.id)
    assert auth.verify_token(tok) == t.id


def test_token_expired_rejected(adb):
    tok = auth.issue_token(42, ttl_s=-10)
    with pytest.raises(auth.AuthError):
        auth.verify_token(tok)


def test_token_tampered_rejected(adb):
    tok = auth.issue_token(42)
    bad = tok[:-4] + ("0000" if not tok.endswith("0000") else "1111")
    with pytest.raises(auth.AuthError):
        auth.verify_token(bad)


def test_authenticate_wrong_password(adb):
    _seed(adb)
    _teacher(adb, "李老师", "li", "pass123")
    with pytest.raises(auth.AuthError):
        auth.authenticate(adb, "li", "wrong!")
    # 用户名不存在同样 AuthError（防时序路径已走）
    with pytest.raises(auth.AuthError):
        auth.authenticate(adb, "nobody", "pass123")


def test_security_mode_off_without_credentials(adb):
    _seed(adb)  # 只有班级与学生，无凭据教师
    assert auth.security_mode_on(adb) is False


def test_security_mode_on_with_credential_teacher(adb):
    _seed(adb)
    _teacher(adb, "李老师", "li", "pass123")
    assert auth.security_mode_on(adb) is True


def test_assert_class_access_matrix(adb):
    """核心矩阵：开放全放行；安全下 甲✓/乙✗/admin✓。"""
    school, kb, c1, c2 = _seed(adb)
    jia = _teacher(adb, "甲老师", "jia", "pass123")
    adb.add(TeacherClass(teacher_id=jia.id, class_id=c1.id))
    adb.flush()
    admin = _teacher(adb, "管理员", "root", "pass123", admin=True)

    anon = auth.AccessContext(teacher=None)
    # 开放模式语义由 security_mode_on 决定——此处已存在带凭据教师，属安全模式，
    # 匿名断言直接拒绝；开放模式（无凭据教师）在 test_security_mode_off 验证。
    with pytest.raises(auth.PermissionError_):
        auth.assert_class_access(adb, anon, c1.id)

    # 进入安全模式（建号即生效）
    ctx_jia = auth.AccessContext(teacher=jia)
    assert auth.assert_class_access(adb, ctx_jia, c1.id).id == c1.id
    with pytest.raises(auth.PermissionError_):
        auth.assert_class_access(adb, ctx_jia, c2.id)

    adb.add(TeacherClass(teacher_id=jia.id, class_id=c2.id))  # 授权后放行
    adb.flush()
    assert auth.assert_class_access(adb, ctx_jia, c2.id).id == c2.id

    ctx_admin = auth.AccessContext(teacher=admin)
    assert auth.assert_class_access(adb, ctx_admin, c2.id).id == c2.id


def test_allowed_class_ids_scoping(adb):
    school, kb, c1, c2 = _seed(adb)
    jia = _teacher(adb, "甲老师", "jia", "pass123")
    anon = auth.AccessContext(teacher=None)
    admin = _teacher(adb, "管理员", "root", "pw12345", admin=True)
    assert auth.allowed_class_ids(adb, anon) is None
    assert auth.allowed_class_ids(adb, auth.AccessContext(teacher=admin)) is None
    got = auth.allowed_class_ids(adb, auth.AccessContext(teacher=jia))
    assert got == []  # 未授权任何班


def test_student_access_follows_class(adb):
    school, kb, c1, c2 = _seed(adb)
    jia = _teacher(adb, "甲老师", "jia", "pass123")
    adb.add(TeacherClass(teacher_id=jia.id, class_id=c1.id))
    adb.flush()
    stu_c2 = adb.query(Student).filter(Student.class_id == c2.id).first()
    with pytest.raises(auth.PermissionError_):
        auth.assert_student_access(
            adb, auth.AccessContext(teacher=jia), stu_c2.id
        )


# ---------------------------------------------------------------------------
# MCP 兜底路线：环境注入身份 → 同一裁决
# ---------------------------------------------------------------------------


def test_mcp_context_from_env(monkeypatch, adb):
    school, kb, c1, c2 = _seed(adb)
    jia = _teacher(adb, "甲老师", "jia", "pass123")
    monkeypatch.setenv("SC_MCP_TEACHER_ID", str(jia.id))
    ctx = auth.mcp_context_from_env(adb)
    assert ctx.teacher is not None and ctx.teacher.id == jia.id
    with pytest.raises(auth.PermissionError_):
        auth.assert_class_access(adb, ctx, c2.id)

    monkeypatch.setenv("SC_MCP_TEACHER_ID", "")
    assert auth.mcp_context_from_env(adb).teacher is None


# ---------------------------------------------------------------------------
# API 层：TestClient 全链路（安全模式）
# ---------------------------------------------------------------------------


@pytest.fixture()
def sec_client(tmp_path):
    """带两个教师（甲授权一班）的安全模式 client；tmp 文件库。"""
    global _ENGINE
    db_file = tmp_path / "auth-api.db"
    eng = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False, autoflush=False)

    from app.api import deps as deps_mod
    from app import db as dbmod
    from app.main import app

    old = (dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal)
    dbmod.engine = eng
    dbmod.SessionLocal = S
    deps_mod.SessionLocal = S

    s = S()
    school, kb, c1, c2 = _seed(s)
    jia = _teacher(s, "甲老师", "jia", "pass123")
    yi = _teacher(s, "乙老师", "yi", "pass123")
    root = _teacher(s, "管理员", "root", "pass123", admin=True)
    s.add(TeacherClass(teacher_id=jia.id, class_id=c1.id))
    s.add(TeacherClass(teacher_id=yi.id, class_id=c2.id))
    s.commit()
    ids = {"c1": c1.id, "c2": c2.id}
    s.close()

    with TestClient(app) as c:
        yield c, S, ids
    dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = old
    eng.dispose()


def _login(client: TestClient, username: str, password: str = "pass123") -> str:
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_api_isolation_teacher_a_cannot_see_b(sec_client):
    """出口判据①端到端：甲看不到乙的班（403），自己的班 200。"""
    client, S, ids = sec_client
    jia_tok = _login(client, "jia")

    ok = client.get(f"/classes/{ids['c1']}/students", headers=_H(jia_tok))
    assert ok.status_code == 200
    denied = client.get(f"/classes/{ids['c2']}/students", headers=_H(jia_tok))
    assert denied.status_code == 403

    # 学生子资源同判：乙班学生的掌握度对甲不可见
    with S() as s:
        stu_b = s.query(Student).filter(Student.class_id == ids["c2"]).first().id
    denied2 = client.get(f"/students/{stu_b}/weaknesses", headers=_H(jia_tok))
    assert denied2.status_code == 403

    # 干预记录列表收敛到授权班级
    rows = client.get("/interventions", headers=_H(jia_tok)).json()
    assert all(it["class_id"] == ids["c1"] for it in rows.get("items", []))


def test_api_anonymous_rejected_in_secure_mode(sec_client):
    client, S, ids = sec_client
    r = client.get(f"/classes/{ids['c1']}/students")
    assert r.status_code == 401
    bad = client.get(f"/classes/{ids['c1']}/students", headers=_H("garbage"))
    assert bad.status_code == 401
    # 白名单探针不受影响
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


def test_api_admin_and_login_flow(sec_client):
    client, S, ids = sec_client
    root_tok = _login(client, "root")
    # admin 建新教师并授权
    r = client.post("/auth/teachers", headers=_H(root_tok), json={
        "name": "新老师", "username": "new", "password": "pass123",
        "school_id": 1,
    })
    assert r.status_code == 200, r.text
    tid = r.json()["teacher_id"]
    g = client.post(f"/auth/teachers/{tid}/classes", headers=_H(root_tok),
                    json={"class_ids": [ids["c1"]]})
    assert g.status_code == 200 and g.json()["added"] == 1
    new_tok = _login(client, "new")
    assert client.get(f"/classes/{ids['c1']}/students", headers=_H(new_tok)).status_code == 200

    # 非 admin 不能建号/授权
    jia_tok = _login(client, "jia")
    assert client.post("/auth/teachers", headers=_H(jia_tok), json={
        "name": "x", "username": "xx", "password": "pass123", "school_id": 1,
    }).status_code == 403
