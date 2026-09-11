"""RBAC 范围体系测试（rbac-scopes-design §10）。

- 单元层：subject_scopes / class_subject（科任绑定覆盖班级默认）/ assert_exam_access
  （学科收窄 + 班主任豁免 + 存量全科授权行不变）/ can_write_kb / can_govern_kb
  范围匹配（未标年级版本按学科匹配）；
- API 层：治理权下放（学科管理员启用本学科版本、他学科 403）、versions 列表
  scope 过滤、KB 写权矩阵（范围内 200 / 范围外 403 / kb_editor 全局 / 普通教师
  粗闸 403）、学生账号开通下放（班主任本班 200 / 非本班 403 / 普通教师 403）、
  subject-scopes 与 homeroom 管理端点、/auth/me 范围字段。
"""

from __future__ import annotations

import secrets

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app import auth
from app.db import Base
from app.models import (
    Class,
    ExamTemplate,
    KbVersion,
    KnowledgePoint,
    School,
    Student,
    Teacher,
    TeacherClass,
    TeacherSubjectScope,
)


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


def _teacher(adb, name, username, admin=False):
    salt = secrets.token_bytes(16)
    t = Teacher(
        school_id=1, name=name, username=username, salt=salt,
        password_hash=auth.hash_password("pass123", salt), admin=admin,
    )
    adb.add(t)
    adb.flush()
    return t


def _mk_kb(adb, subject, version, status, grade=None):
    kb = KbVersion(subject=subject, textbook_edition="人教版", version=version,
                   status=status, grade=grade)
    adb.add(kb)
    adb.flush()
    return kb


# ---------------------------------------------------------------------------
# 单元层：范围判定
# ---------------------------------------------------------------------------


def test_subject_scopes_admin_none_teacher_rows(adb):
    admin = _teacher(adb, "管理员", "root", admin=True)
    t = _teacher(adb, "科管", "krm")
    adb.add(TeacherSubjectScope(teacher_id=t.id, subject="数学", grade=7))
    adb.add(TeacherSubjectScope(teacher_id=t.id, subject="物理", grade=8))
    adb.flush()
    assert auth.subject_scopes(adb, auth.AccessContext(teacher=admin)) is None
    assert auth.subject_scopes(adb, auth.AccessContext()) is None
    assert sorted(auth.subject_scopes(adb, auth.AccessContext(teacher=t))) == [
        ("数学", 7), ("物理", 8)
    ]
    assert auth.subject_scopes(adb, auth.AccessContext(teacher=_teacher(adb, "普通", "plain"))) == []


def test_class_subject_bound_row_overrides_default(adb):
    c1 = Class(school_id=1, name="甲班", grade=7, subject="数学")
    adb.add(c1)
    adb.flush()
    t = _teacher(adb, "科任", "ke")
    adb.add(TeacherClass(teacher_id=t.id, class_id=c1.id, subject="物理"))
    # 同班全科授权的另一教师
    t2 = _teacher(adb, "全科", "quan")
    adb.add(TeacherClass(teacher_id=t2.id, class_id=c1.id, subject=None))
    adb.flush()
    # 科任绑定覆盖班级默认学科
    assert auth.class_subject(adb, auth.AccessContext(teacher=t), c1) == "物理"
    # 全科授权 / 班主任 / admin / 匿名 → 班级默认
    assert auth.class_subject(adb, auth.AccessContext(teacher=t2), c1) == "数学"
    hr = _teacher(adb, "班主任", "ban")
    c1.homeroom_teacher_id = hr.id
    adb.flush()
    assert auth.class_subject(adb, auth.AccessContext(teacher=hr), c1) == "数学"
    assert auth.class_subject(adb, auth.AccessContext(), c1) == "数学"


def test_assert_exam_access_subject_narrowing_and_homeroom(adb):
    c1 = Class(school_id=1, name="甲班", grade=7, subject="数学")
    adb.add(c1)
    adb.flush()
    e_math = ExamTemplate(class_id=c1.id, name="数学期中", exam_date=__import__("datetime").date(2026, 9, 1), type="期中", subject="数学")
    e_phys = ExamTemplate(class_id=c1.id, name="物理单元", exam_date=__import__("datetime").date(2026, 9, 2), type="单元", subject="物理")
    e_legacy = ExamTemplate(class_id=c1.id, name="存量未标注", exam_date=__import__("datetime").date(2026, 9, 3), type="单元", subject=None)
    adb.add_all([e_math, e_phys, e_legacy])
    adb.flush()

    ker = _teacher(adb, "数学科任", "ke")
    adb.add(TeacherClass(teacher_id=ker.id, class_id=c1.id, subject="数学"))
    hr = _teacher(adb, "班主任", "ban")
    c1.homeroom_teacher_id = hr.id
    all_t = _teacher(adb, "全科授权", "quan")
    adb.add(TeacherClass(teacher_id=all_t.id, class_id=c1.id, subject=None))
    plain = _teacher(adb, "无授权", "plain")
    admin = _teacher(adb, "管理员", "root", admin=True)
    adb.flush()

    ctx_ke = auth.AccessContext(teacher=ker)
    ctx_hr = auth.AccessContext(teacher=hr)
    ctx_admin = auth.AccessContext(teacher=admin)

    # 科任：本学科 + 存量未标注（回退班级默认=数学）可见；他学科拒绝
    auth.assert_exam_access(adb, ctx_ke, e_math)
    auth.assert_exam_access(adb, ctx_ke, e_legacy)
    with pytest.raises(auth.PermissionError_):
        auth.assert_exam_access(adb, ctx_ke, e_phys)
    # 班主任全科豁免；admin 不收窄
    auth.assert_exam_access(adb, ctx_hr, e_phys)
    auth.assert_exam_access(adb, ctx_admin, e_phys)
    auth.assert_exam_access(adb, auth.AccessContext(teacher=all_t), e_phys)
    # 无授权行 → 班级层拒绝（assert_class_access）
    with pytest.raises(auth.PermissionError_):
        auth.assert_exam_access(adb, auth.AccessContext(teacher=plain), e_math)


def test_can_write_and_govern_kb_scope_matching(adb):
    t = _teacher(adb, "科管", "krm")
    adb.add(TeacherSubjectScope(teacher_id=t.id, subject="数学", grade=7))
    ker = _teacher(adb, "kb编辑", "ker")
    ker.kb_editor = True
    adb.flush()
    ctx = auth.AccessContext(teacher=t)

    # 范围内 / 外
    assert auth.can_write_kb(adb, ctx, "数学", 7) is True
    assert auth.can_govern_kb(adb, ctx, "数学", 7) is True
    assert auth.can_write_kb(adb, ctx, "物理", 7) is False
    assert auth.can_govern_kb(adb, ctx, "物理", 7) is False
    assert auth.can_write_kb(adb, ctx, "数学", 8) is False
    # 未标年级版本（存量）按学科匹配
    assert auth.can_write_kb(adb, ctx, "数学", None) is True
    assert auth.can_govern_kb(adb, ctx, "数学", None) is True
    # kb_editor：内容写全局，治理仍要范围
    ctx_ker = auth.AccessContext(teacher=ker)
    assert auth.can_write_kb(adb, ctx_ker, "物理", 9) is True
    assert auth.can_govern_kb(adb, ctx_ker, "数学", 7) is False


# ---------------------------------------------------------------------------
# API 层
# ---------------------------------------------------------------------------


@pytest.fixture()
def rbac_client(tmp_path):
    db_file = tmp_path / "rbac.db"
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
    c2 = Class(school_id=school.id, name="乙班", grade=7, subject="数学")
    s.add_all([c1, c2])
    s.flush()

    math_active = _mk_kb(s, "数学", "1.0", "active", grade=7)
    math_draft = _mk_kb(s, "数学", "1.1", "draft", grade=7)
    phys_active = _mk_kb(s, "物理", "1.0", "active", grade=7)
    phys_draft = _mk_kb(s, "物理", "1.1", "draft", grade=7)
    for kb in (math_active, math_draft, phys_active, phys_draft):
        s.add(KnowledgePoint(kb_version_id=kb.id, code=f"K{kb.id}1", name="点",
                             grade=7, semester=1, cog_levels_expected=["应用"],
                             difficulty_prior=0.5, mastery_floor=0.6))

    root = _teacher(s, "管理员", "root", admin=True)
    krm = _teacher(s, "科管", "krm")
    s.add(TeacherSubjectScope(teacher_id=krm.id, subject="数学", grade=7))
    ban = _teacher(s, "班主任", "ban")
    c1.homeroom_teacher_id = ban.id
    ker = _teacher(s, "kb编辑", "ker")
    ker.kb_editor = True
    plain = _teacher(s, "普通", "plain")
    stu1 = Student(school_id=school.id, class_id=c1.id, name_or_alias="学生一", external_code="S001")
    stu2 = Student(school_id=school.id, class_id=c2.id, name_or_alias="学生二", external_code="S002")
    s.add_all([stu1, stu2])
    s.commit()
    ids = {
        "c1": c1.id, "c2": c2.id,
        "stu1": stu1.id, "stu2": stu2.id,
        "math_active": math_active.id, "math_draft": math_draft.id,
        "phys_active": phys_active.id, "phys_draft": phys_draft.id,
    }
    s.close()

    with TestClient(app) as c:
        yield c, S, ids
    dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = old
    eng.dispose()


def _login(client, username):
    r = client.post("/auth/login", json={"username": username, "password": "pass123"})
    assert r.status_code == 200, r.text
    return r.json()


def _H(token):
    return {"Authorization": f"Bearer {token}"}


def test_subject_admin_governs_own_subject_only(rbac_client):
    client, S, ids = rbac_client
    tok = _login(client, "krm")["token"]

    # 治理权下放：启用本学科（数学）草稿 → 200，同学科旧 active 降级、他学科不动
    r = client.patch(f"/kb/versions/{ids['math_draft']}?confirm=true&force=true",
                     headers=_H(tok), json={"status": "active"})
    assert r.status_code == 200, r.text
    with S() as s:
        st = {kb.id: kb.status for kb in s.query(KbVersion).all()}
    assert st[ids["math_draft"]] == "active"
    assert st[ids["math_active"]] == "reviewed"
    assert st[ids["phys_active"]] == "active"

    # 他学科治理 → 403
    r = client.patch(f"/kb/versions/{ids['phys_draft']}?confirm=true&force=true",
                     headers=_H(tok), json={"status": "active"})
    assert r.status_code == 403


def test_versions_list_filtered_by_scope(rbac_client):
    client, S, ids = rbac_client
    krm = _login(client, "krm")["token"]
    subjects = {v["subject"] for v in client.get("/kb/versions", headers=_H(krm)).json()["versions"]}
    assert subjects == {"数学"}

    # 普通教师 / kb_editor：读全量（KB 读=全校既有语义）
    plain = _login(client, "plain")["token"]
    subjects_plain = {v["subject"] for v in client.get("/kb/versions", headers=_H(plain)).json()["versions"]}
    assert subjects_plain == {"数学", "物理"}
    ker = _login(client, "ker")["token"]
    subjects_ker = {v["subject"] for v in client.get("/kb/versions", headers=_H(ker)).json()["versions"]}
    assert subjects_ker == {"数学", "物理"}


def test_kb_write_matrix_by_scope(rbac_client):
    client, S, ids = rbac_client
    krm = _login(client, "krm")["token"]
    ker = _login(client, "ker")["token"]
    plain = _login(client, "plain")["token"]

    # 范围内写入 200
    r = client.post("/kb/kps", headers=_H(krm),
                    json={"code": "MK1", "name": "科管点", "grade": 7, "kb_version_id": ids["math_draft"]})
    assert r.status_code == 200, r.text
    # 范围外写入 403
    r = client.post("/kb/kps", headers=_H(krm),
                    json={"code": "PK1", "name": "越权点", "grade": 7, "kb_version_id": ids["phys_draft"]})
    assert r.status_code == 403
    # kb_editor 全局内容写
    r = client.post("/kb/kps", headers=_H(ker),
                    json={"code": "PK2", "name": "编辑点", "grade": 7, "kb_version_id": ids["phys_draft"]})
    assert r.status_code == 200, r.text
    # 普通教师粗闸 403
    r = client.post("/kb/kps", headers=_H(plain),
                    json={"code": "MK2", "name": "普通点", "grade": 7, "kb_version_id": ids["math_draft"]})
    assert r.status_code == 403


def test_student_enable_delegated_to_homeroom(rbac_client):
    client, S, ids = rbac_client
    ban = _login(client, "ban")["token"]
    plain = _login(client, "plain")["token"]
    root = _login(client, "root")["token"]

    # 班主任开通本班学生 → 200 且可登录
    r = client.post(f"/auth/students/{ids['stu1']}/enable", headers=_H(ban),
                    json={"password": "stupass1"})
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"username": "S001", "password": "stupass1"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "student"

    # 非本班班主任（甲班班主任 开 乙班学生）→ 403
    r = client.post(f"/auth/students/{ids['stu2']}/enable", headers=_H(ban),
                    json={"password": "stupass2"})
    assert r.status_code == 403
    # 普通教师 → 403
    r = client.post(f"/auth/students/{ids['stu1']}/enable", headers=_H(plain),
                    json={"password": "stupass3"})
    assert r.status_code == 403
    # admin 仍可（重置）
    r = client.post(f"/auth/students/{ids['stu1']}/enable", headers=_H(root),
                    json={"password": "stupass4"})
    assert r.status_code == 200, r.text


def test_subject_scope_and_homeroom_admin_endpoints(rbac_client):
    client, S, ids = rbac_client
    root = _login(client, "root")["token"]
    plain_tok = _login(client, "plain")["token"]
    plain_id = None
    with S() as s:
        plain_id = s.query(Teacher).filter_by(username="plain").one().id

    # 非 admin 不可设授权
    r = client.put(f"/auth/teachers/{plain_id}/subject-scopes", headers=_H(plain_tok),
                   json={"scopes": [{"subject": "数学", "grade": 7}]})
    assert r.status_code == 403

    # admin 覆盖式设置两行
    r = client.put(f"/auth/teachers/{plain_id}/subject-scopes", headers=_H(root),
                   json={"scopes": [{"subject": "数学", "grade": 7}, {"subject": "物理", "grade": 8}]})
    assert r.status_code == 200, r.text
    with S() as s:
        rows = sorted(
            (x.subject, x.grade)
            for x in s.query(TeacherSubjectScope).filter_by(teacher_id=plain_id).all()
        )
    assert rows == [("数学", 7), ("物理", 8)]

    # GET /auth/teachers 带出 scopes + homeroom
    body = client.get("/auth/teachers", headers=_H(root)).json()
    row = next(t for t in body["teachers"] if t["teacher_id"] == plain_id)
    assert row["subject_scopes"] == [{"subject": "数学", "grade": 7}, {"subject": "物理", "grade": 8}]

    # /auth/me 范围字段：plain 现在有学科授权；ban 有班主任班级
    me = client.get("/auth/me", headers=_H(plain_tok)).json()
    assert me["subject_scopes"] == [{"subject": "数学", "grade": 7}, {"subject": "物理", "grade": 8}]
    ban_me = client.get("/auth/me", headers=_H(_login(client, "ban")["token"])).json()
    assert ids["c1"] in ban_me["homeroom_class_ids"]

    # 班主任指派/取消
    r = client.put(f"/auth/classes/{ids['c2']}/homeroom", headers=_H(root),
                   json={"teacher_id": plain_id})
    assert r.status_code == 200, r.text
    me2 = client.get("/auth/me", headers=_H(plain_tok)).json()
    assert ids["c2"] in me2["homeroom_class_ids"]
    r = client.put(f"/auth/classes/{ids['c2']}/homeroom", headers=_H(root),
                   json={"teacher_id": None})
    assert r.status_code == 200, r.text
    with S() as s:
        assert s.query(Class).get(ids["c2"]).homeroom_teacher_id is None
    # 不存在的教师 → 404
    assert client.put(f"/auth/classes/{ids['c2']}/homeroom", headers=_H(root),
                      json={"teacher_id": 99999}).status_code == 404
