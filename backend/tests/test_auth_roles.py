"""三角色登录鉴权测试（auth-roles-design §3-6）。

- 单元层：token kind（t/s）、verify_token 拒绝 student、AccessContext.role、
  authenticate_any 教师优先、enable_student_login 唯一性；
- API 层：安全模式下学生 token 可读 /auth/me+/me，撞教师端点 403；教师撞
  /me 403；admin 开通学生账号后可登录；学生只见 issued 报告。
"""

from __future__ import annotations

import secrets

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app import auth
from app.db import Base
from app.models import Class, KbVersion, KnowledgePoint, Report, School, Student, Teacher


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


def _pw(user_type: str, who: int) -> tuple[str, str, bytes]:
    salt = secrets.token_bytes(16)
    password = "pass123"
    return f"{user_type}{who}", password, salt


def _teacher(adb, name, username, password, admin=False):
    salt = secrets.token_bytes(16)
    t = Teacher(
        school_id=1, name=name, username=username, salt=salt,
        password_hash=auth.hash_password(password, salt), admin=admin,
    )
    adb.add(t)
    adb.flush()
    return t


def _student(adb, class_id, alias, *, external_code=""):
    s = Student(school_id=1, class_id=class_id, name_or_alias=alias,
                external_code=external_code)
    adb.add(s)
    adb.flush()
    return s


# ---------------------------------------------------------------------------
# 单元层
# ---------------------------------------------------------------------------


def test_token_kind_roundtrip_and_teacher_scoped_reject(adb):
    t = _teacher(adb, "李老师", "li", "pass123")
    tk = auth.issue_token(t.id)
    assert auth.verify_principal_token(tk) == ("t", t.id)
    assert auth.verify_token(tk) == t.id

    s = _student(adb, 1, "学生甲", external_code="S001")
    st = auth.issue_student_token(s.id)
    assert auth.verify_principal_token(st) == ("s", s.id)
    # 教师专用解析拒绝 student token
    with pytest.raises(auth.AuthError):
        auth.verify_token(st)


def test_access_context_role_mapping(adb):
    t = _teacher(adb, "普通教师", "jia", "pass123")
    admin = _teacher(adb, "管理员", "root", "pass123", admin=True)
    s = _student(adb, 1, "学生乙", external_code="S002")
    assert auth.AccessContext(teacher=t).role == "teacher"
    assert auth.AccessContext(teacher=t).is_admin is False
    assert auth.AccessContext(teacher=admin).role == "admin"
    assert auth.AccessContext(teacher=admin).is_admin is True
    assert auth.AccessContext(student=s).role == "student"
    assert auth.AccessContext().role == "anonymous"
    assert auth.AccessContext(student=s).principal.id == s.id


def test_authenticate_any_teacher_precedence_then_student(adb):
    _teacher(adb, "李老师", "li", "pass123")
    s = _student(adb, 1, "学生丙", external_code="S003")
    auth.enable_student_login(adb, s.id, "studentpw", username="stu003")
    adb.flush()

    p, _tok, kind = auth.authenticate_any(adb, "li", "pass123")
    assert kind == "t" and isinstance(p, Teacher)
    p2, _tok2, kind2 = auth.authenticate_any(adb, "stu003", "studentpw")
    assert kind2 == "s" and isinstance(p2, Student)
    with pytest.raises(auth.AuthError):
        auth.authenticate_any(adb, "stu003", "wrong!")
    with pytest.raises(auth.AuthError):
        auth.authenticate_any(adb, "nobody", "pass123")


def test_enable_student_login_uniqueness(adb):
    s1 = _student(adb, 1, "甲", external_code="S101")
    s2 = _student(adb, 1, "乙", external_code="S101")  # 同班同学籍号（未开号允许）
    _teacher(adb, "管理员", "root", "pass123", admin=True)
    uname1 = auth.enable_student_login(adb, s1.id, "pw12345")[1]
    assert uname1 == "S101"
    # s2 撞名 s1 → 拒绝
    with pytest.raises(ValueError):
        auth.enable_student_login(adb, s2.id, "pw12345", username="S101")
    # 撞 teacher 用户名 → 拒绝
    with pytest.raises(ValueError):
        auth.enable_student_login(adb, s2.id, "pw12345", username="root")


# ---------------------------------------------------------------------------
# API 层：安全模式角色隔离
# ---------------------------------------------------------------------------


@pytest.fixture()
def sec_client(tmp_path):
    db_file = tmp_path / "auth-roles.db"
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
    school = School(name="测试学校")
    s.add(school)
    s.flush()
    c1 = Class(school_id=school.id, name="甲班", grade=7, subject="数学")
    s.add(c1)
    s.flush()
    kb = KbVersion(subject="数学", textbook_edition="t", version="1", status="active")
    s.add(kb)
    s.flush()
    for code in ("M1", "M2"):
        s.add(KnowledgePoint(kb_version_id=kb.id, code=code, name=f"点{code}",
                             grade=7, semester=1, cog_levels_expected=["应用"],
                             difficulty_prior=0.5, mastery_floor=0.6))
    jia = _teacher(s, "甲老师", "jia", "pass123")
    root = _teacher(s, "管理员", "root", "pass123", admin=True)
    stu = _student(s, c1.id, "学生丁", external_code="S004")
    auth.enable_student_login(s, stu.id, "studentpw", username="stu004")
    s.commit()
    ids = {"c1": c1.id, "stu": stu.id, "kb": kb.id}
    s.close()

    with TestClient(app) as c:
        yield c, S, ids
    dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = old
    eng.dispose()


def _login(client, username, password):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _H(token):
    return {"Authorization": f"Bearer {token}"}


def test_student_self_read_isolated_from_teacher_surface(sec_client):
    client, S, ids = sec_client
    body = _login(client, "stu004", "studentpw")
    assert body["role"] == "student"
    assert body["student"]["class_id"] == ids["c1"]
    tok = body["token"]

    me = client.get("/auth/me", headers=_H(tok))
    assert me.status_code == 200 and me.json()["role"] == "student"

    assert client.get("/me", headers=_H(tok)).status_code == 200
    assert client.get("/me/mastery", headers=_H(tok)).status_code == 200
    assert client.get("/me/weaknesses", headers=_H(tok)).status_code == 200
    assert client.get("/me/reports", headers=_H(tok)).status_code == 200

    # 学生撞教师端点 → 中间件 403（不是 401）
    assert client.get("/classes", headers=_H(tok)).status_code == 403
    assert client.get(f"/classes/{ids['c1']}/students", headers=_H(tok)).status_code == 403
    assert client.get(f"/students/{ids['stu']}/weaknesses", headers=_H(tok)).status_code == 403


def test_teacher_cannot_hit_student_portal(sec_client):
    client, S, ids = sec_client
    tok = _login(client, "jia", "pass123")["token"]
    assert client.get("/me", headers=_H(tok)).status_code == 403


def test_admin_enable_student_via_api_then_login(sec_client):
    client, S, ids = sec_client
    root_tok = _login(client, "root", "pass123")["token"]
    with S() as s:
        stu2 = _student(s, ids["c1"], "学生戊", external_code="S005")
        s.commit()
        stu2_id = stu2.id
    r = client.post(f"/auth/students/{stu2_id}/enable", headers=_H(root_tok),
                    json={"password": "newpw123", "username": "stu005"})
    assert r.status_code == 200, r.text
    body = _login(client, "stu005", "newpw123")
    assert body["role"] == "student"
    # 非 admin 教师不能开通学生账号
    jia_tok = _login(client, "jia", "pass123")["token"]
    assert client.post(f"/auth/students/{stu2_id}/enable", headers=_H(jia_tok),
                       json={"password": "x12345"}).status_code == 403


def test_student_reports_only_issued(sec_client):
    client, S, ids = sec_client
    with S() as s:
        s.add(Report(type="student_diagnosis", class_id=ids["c1"],
                     student_id=ids["stu"], status="draft",
                     content_markdown="# draft", snapshot_json={}))
        s.add(Report(type="student_action_plan", class_id=ids["c1"],
                     student_id=ids["stu"], status="issued",
                     content_markdown="# issued", snapshot_json={}))
        s.commit()
    tok = _login(client, "stu004", "studentpw")["token"]
    reports = client.get("/me/reports", headers=_H(tok)).json()["reports"]
    assert len(reports) == 1 and reports[0]["type"] == "student_action_plan"
    # draft 不可见（404）
    drafts = client.get("/me/reports", headers=_H(tok)).json()["reports"]
    assert all(d["type"] != "student_diagnosis" for d in drafts)


# ---------------------------------------------------------------------------
# 管理端只读面（frontend-ends-design §C/§D）+ KB 写权矩阵（§B）
# ---------------------------------------------------------------------------


def _kp_payload(code="M9", name="新知识点"):
    return {"code": code, "name": name, "grade": 7}


def test_kb_write_gate_secure_mode(sec_client):
    """KB 写基线（两层写权）：未授权教师 403、匿名 401（中间件）；admin 可写。
    授权教师的完整矩阵见 test_kb_editor_grant_matrix。读端点教师可读。"""
    client, _S, ids = sec_client
    root = _login(client, "root", "pass123")["token"]
    jia = _login(client, "jia", "pass123")["token"]

    # 读：admin 与普通教师都通
    assert client.get("/kb/kps", headers=_H(root)).status_code == 200
    assert client.get("/kb/kps", headers=_H(jia)).status_code == 200

    # 写：匿名（安全模式）401、未授权教师 403、admin 200
    assert client.post("/kb/kps", json=_kp_payload()).status_code == 401
    assert client.post("/kb/kps", headers=_H(jia), json=_kp_payload()).status_code == 403
    r = client.post("/kb/kps", headers=_H(root), json=_kp_payload())
    assert r.status_code == 200, r.text

    # 版本 fork / 激活切换同样是写操作
    assert client.post("/kb/versions", headers=_H(jia)).status_code == 403
    forked = client.post("/kb/versions", headers=_H(root)).json()["id"]
    assert client.patch(f"/kb/versions/{forked}", headers=_H(jia),
                        json={"status": "active"}).status_code == 403
    assert client.patch(f"/kb/versions/{forked}", headers=_H(root),
                        json={"status": "active"}).status_code in (200, 409)


def test_kb_editor_grant_matrix(sec_client):
    """两层写权（frontend-ends-design §B）：授权教师内容层可写（kp/关系/fork 草稿/
    draft→reviewed），版本治理（设为正式版）仍 admin；授权对已发 token 即时生效/
    撤销（角色从 DB 现读，token 无状态）。"""
    client, _S, _ids = sec_client
    root = _login(client, "root", "pass123")["token"]
    jia = _login(client, "jia", "pass123")["token"]

    rows = {
        t["name"]: t
        for t in client.get("/auth/teachers", headers=_H(root)).json()["teachers"]
    }
    jia_id = rows["甲老师"]["teacher_id"]
    assert rows["甲老师"]["kb_editor"] is False  # 列表暴露授权态

    # 授权端点 admin 专属
    assert client.post(
        f"/auth/teachers/{jia_id}/kb-editor", headers=_H(jia),
        json={"kb_editor": True},
    ).status_code == 403
    r = client.post(f"/auth/teachers/{jia_id}/kb-editor", headers=_H(root),
                    json={"kb_editor": True})
    assert r.status_code == 200 and r.json()["kb_editor"] is True

    # 内容层：同一旧 token 立即获得写权
    r = client.post("/kb/kps", headers=_H(jia), json=_kp_payload(code="M10"))
    assert r.status_code == 200, r.text
    forked = client.post("/kb/versions", headers=_H(jia)).json()["id"]
    assert client.patch(f"/kb/versions/{forked}", headers=_H(jia),
                        json={"status": "reviewed"}).status_code == 200

    # 版本治理：设为正式版仍 403（admin 专属）；admin 照常
    assert client.patch(f"/kb/versions/{forked}", headers=_H(jia),
                        json={"status": "active"}).status_code == 403
    assert client.patch(f"/kb/versions/{forked}", headers=_H(root),
                        json={"status": "active"}).status_code in (200, 409)

    # 撤销 → 同一 token 立即回到只读
    client.post(f"/auth/teachers/{jia_id}/kb-editor", headers=_H(root),
                json={"kb_editor": False})
    assert client.post("/kb/kps", headers=_H(jia),
                       json=_kp_payload(code="M11")).status_code == 403


def test_list_teachers_admin_only_shape(sec_client):
    client, _S, _ids = sec_client
    jia = _login(client, "jia", "pass123")["token"]
    root = _login(client, "root", "pass123")["token"]

    assert client.get("/auth/teachers", headers=_H(jia)).status_code == 403
    r = client.get("/auth/teachers", headers=_H(root))
    assert r.status_code == 200, r.text
    rows = {t["name"]: t for t in r.json()["teachers"]}
    assert "管理员" in rows and "甲老师" in rows
    t = rows["甲老师"]
    assert t["username"] == "jia" and t["admin"] is False
    assert t["kb_editor"] is False
    assert isinstance(t["classes"], list)
    assert t["teacher_id"] > 0


def test_list_students_exposes_account_state(sec_client):
    """list_students 增 has_account/username（frontend-ends-design §D）：未开通 null/False，
    开通后 True/登录名。admin 视角（教师需班级授权，甲老师未授权走 403 已在别处覆盖）。"""
    client, S, ids = sec_client
    root = _login(client, "root", "pass123")["token"]
    # ids["stu"] = 学生丁 S004 已被 enable_student_login(username=stu004)
    rows = client.get(f"/classes/{ids['c1']}/students", headers=_H(root)).json()["students"]
    by_alias = {s["name_or_alias"]: s for s in rows}
    enabled = by_alias["学生丁"]
    assert enabled["has_account"] is True
    assert enabled["username"] == "stu004"
    # 新建未开通学生 → False / None
    with S() as s:
        stu2 = _student(s, ids["c1"], "学生己", external_code="S006")
        s.commit()
        stu2_id = stu2.id
    rows2 = client.get(f"/classes/{ids['c1']}/students", headers=_H(root)).json()["students"]
    fresh = next(s for s in rows2 if s["student_id"] == stu2_id)
    assert fresh["has_account"] is False and fresh["username"] is None


# ---------------------------------------------------------------------------
# 学生门户预览（超级账号：admin 只读镜像 /admin/students/{id}/portal/*）
# ---------------------------------------------------------------------------


def test_student_portal_preview_admin_only(sec_client):
    """预览端点：学生 token 中间件 403、普通教师 403、安全模式匿名 401、admin 200；
    五条只读面齐备。"""
    client, _S, ids = sec_client
    stu_id = ids["stu"]
    base = f"/admin/students/{stu_id}/portal"

    stu_tok = _login(client, "stu004", "studentpw")["token"]
    assert client.get(base, headers=_H(stu_tok)).status_code == 403
    jia_tok = _login(client, "jia", "pass123")["token"]
    assert client.get(base, headers=_H(jia_tok)).status_code == 403
    assert client.get(base).status_code == 401

    root = _login(client, "root", "pass123")["token"]
    r = client.get(base, headers=_H(root))
    assert r.status_code == 200, r.text
    assert r.json()["student"]["id"] == stu_id
    for sub in ("mastery", "weaknesses", "reports", "action-plan"):
        assert client.get(f"{base}/{sub}", headers=_H(root)).status_code == 200, sub


def test_student_portal_preview_matches_me_and_is_read_only(sec_client):
    """预览与 /me 同源：逐字相等、draft 不可见；且是只读——不触发生成/落库。"""
    client, S, ids = sec_client
    stu_id = ids["stu"]
    with S() as s:
        draft = Report(type="student_diagnosis", class_id=ids["c1"], student_id=stu_id,
                       status="draft", content_markdown="# draft", snapshot_json={})
        issued = Report(type="student_action_plan", class_id=ids["c1"], student_id=stu_id,
                        status="issued", content_markdown="# issued",
                        snapshot_json={"as_of": "2026-01-01"})
        s.add(draft)
        s.add(issued)
        s.commit()
        draft_id, issued_id = draft.id, issued.id
        n_before = s.scalar(select(func.count(Report.id)))

    root = _login(client, "root", "pass123")["token"]
    stu_tok = _login(client, "stu004", "studentpw")["token"]
    base = f"/admin/students/{stu_id}/portal"

    # reports 列表与 /me 逐字相等；draft 不出现
    prev = client.get(f"{base}/reports", headers=_H(root)).json()
    own = client.get("/me/reports", headers=_H(stu_tok)).json()
    assert prev == own
    assert [r["type"] for r in prev["reports"]] == ["student_action_plan"]

    # draft 详情 404（与学生本人同）；issued 详情可读
    assert client.get(f"{base}/reports/{draft_id}/full", headers=_H(root)).status_code == 404
    assert client.get(f"{base}/reports/{issued_id}/full", headers=_H(root)).status_code == 200

    # 只读：预览全部端点后报告总数不变（不 get-or-generate）
    for sub in ("", "/mastery", "/weaknesses", "/reports", "/action-plan"):
        assert client.get(f"{base}{sub}", headers=_H(root)).status_code == 200
    with S() as s:
        assert s.scalar(select(func.count(Report.id))) == n_before


def test_student_portal_preview_unknown_student_404(sec_client):
    client, _S, _ids = sec_client
    root = _login(client, "root", "pass123")["token"]
    assert client.get("/admin/students/99999/portal", headers=_H(root)).status_code == 404
