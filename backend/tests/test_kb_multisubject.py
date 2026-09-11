"""多学科知识库口径测试（2026-09-11 图形化建库 + 多学科管理）。

- 单元层：active_kb(subject) 学科隔离——同学科内 active/兜底，绝不跨学科兜底；
  strict 模式学科内无 active 抛错；
- API 层：PATCH 激活只降**同学科**旧 active（别学科 active 不动）；
  POST /kb/versions/create 建空白草稿版本；POST /kb/kps 带 kb_version_id 写入
  draft/reviewed 版本、拒绝直写 active 版本。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.kb.resolver import KbNotActiveError, active_kb
from app.db import Base
from app.models import KbVersion, KnowledgePoint


@pytest.fixture()
def adb():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    s = S()
    yield s
    s.close()
    engine.dispose()


def _kb(adb, subject, version, status):
    kb = KbVersion(subject=subject, textbook_edition="人教版", version=version, status=status)
    adb.add(kb)
    adb.flush()
    return kb


# ---------------------------------------------------------------------------
# 单元层：学科解析
# ---------------------------------------------------------------------------


def test_active_kb_resolves_within_subject(adb):
    math_active = _kb(adb, "数学", "1.0", "active")
    cn_active = _kb(adb, "语文", "1.0", "active")
    en_draft = _kb(adb, "英语", "0.9", "draft")

    # 全局（subject=None）= 最新 active（旧行为兼容）
    assert active_kb(adb).id == cn_active.id
    # 学科口径：各自学科内解析，互不串台
    assert active_kb(adb, "数学").id == math_active.id
    assert active_kb(adb, "语文").id == cn_active.id
    # 学科内无 active → 非严格兜底**同学科**最新 draft（绝不跨学科拿别科 active）
    assert active_kb(adb, "英语").id == en_draft.id
    # 学科无任何版本 → None（不跨学科兜底）
    assert active_kb(adb, "科学") is None


def test_active_kb_subject_never_crosses_subjects(adb):
    _kb(adb, "数学", "1.0", "active")
    # 语文没有任何版本 → None（不回落数学）
    assert active_kb(adb, "语文") is None


def test_active_kb_strict_raises_within_subject(adb, monkeypatch):
    _kb(adb, "数学", "1.0", "active")
    _kb(adb, "语文", "0.9", "draft")
    monkeypatch.setenv("SC_KB_STRICT_ACTIVE", "1")
    # 数学有 active → 正常
    assert active_kb(adb, "数学") is not None
    # 语文只有 draft → strict 抛错（学科名进错误信息）
    with pytest.raises(KbNotActiveError) as ei:
        active_kb(adb, "语文")
    assert "语文" in str(ei.value)


# ---------------------------------------------------------------------------
# API 层：激活互斥 / 建版本 / 指定版本建 KP
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path):
    db_file = tmp_path / "kb-multi.db"
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
    math_active = _kb(s, "数学", "1.0", "active")
    for code in ("M1", "M2"):
        s.add(KnowledgePoint(kb_version_id=math_active.id, code=code, name=f"点{code}",
                             grade=7, semester=1, cog_levels_expected=["应用"],
                             difficulty_prior=0.5, mastery_floor=0.6))
    math_draft = _kb(s, "数学", "1.1", "draft")
    for code in ("M1", "M2"):
        s.add(KnowledgePoint(kb_version_id=math_draft.id, code=code, name=f"点{code}新",
                             grade=7, semester=1, cog_levels_expected=["应用"],
                             difficulty_prior=0.5, mastery_floor=0.6))
    cn_active = _kb(s, "语文", "1.0", "active")
    s.commit()
    ids = {"math_active": math_active.id, "math_draft": math_draft.id, "cn": cn_active.id}
    s.close()

    with TestClient(app) as c:
        yield c, S, ids
    dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = old
    eng.dispose()


def test_activate_demotes_same_subject_only(client):
    client, S, ids = client
    r = client.patch(
        f"/kb/versions/{ids['math_draft']}?confirm=true&force=true",
        json={"status": "active"},
    )
    assert r.status_code == 200, r.text
    s = S()
    statuses = {kb.id: kb.status for kb in s.query(KbVersion).all()}
    s.close()
    # 数学内互斥：新 active 就位、旧 active 降 reviewed
    assert statuses[ids["math_draft"]] == "active"
    assert statuses[ids["math_active"]] == "reviewed"
    # 语文的 active 不受数学激活影响
    assert statuses[ids["cn"]] == "active"


def test_create_empty_version_endpoint(client):
    client, S, _ = client
    r = client.post(
        "/kb/versions/create",
        json={"subject": "物理", "textbook_edition": "人教版", "version": "0.1.0"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "draft"
    assert body["subject"] == "物理"
    s = S()
    kb = s.get(KbVersion, body["id"])
    s.close()
    assert kb is not None and kb.subject == "物理" and kb.status == "draft"


def test_create_kp_into_explicit_version(client):
    client, S, ids = client
    # 向导路径：写入 draft 版本
    r = client.post(
        "/kb/kps",
        json={"code": "M9", "name": "新点", "grade": 7, "kb_version_id": ids["math_draft"]},
    )
    assert r.status_code == 200, r.text
    s = S()
    kp = s.query(KnowledgePoint).filter_by(code="M9").one()
    s.close()
    assert kp.kb_version_id == ids["math_draft"]

    # 治理路径：拒绝直写 active 版本
    r = client.post(
        "/kb/kps",
        json={"code": "MX", "name": "直写", "grade": 7, "kb_version_id": ids["math_active"]},
    )
    assert r.status_code == 400
