"""GET /classes/overview：一级「班级概览」汇总接口。

复用 test_kb_edit 的临时库隔离模式：替换 app.db 与 routes 两处 SessionLocal。
"""

from __future__ import annotations

from pathlib import Path

KB_YAML = Path(__file__).resolve().parents[1] / "kb" / "math" / "grade7" / "kb.yaml"

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path):
    """临时库隔离：替换 app.db 与 deps 两处 SessionLocal。"""
    db_path = tmp_path / "overview_test.db"
    import app.api.deps as deps_mod
    import app.db as dbmod
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    from app.db import Base
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)
    new_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    original = (dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal)
    dbmod.engine, dbmod.SessionLocal = engine, new_session
    deps_mod.SessionLocal = new_session
    with TestClient(app) as c:
        yield c, new_session
    dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = original


def _bootstrap(c: TestClient) -> tuple[int, int]:
    """导入 kb + 建 school/class（2 学生）。返回 (class_id, kb_version_id)。"""
    assert c.post("/kb/import", json={"yaml_path": str(KB_YAML)}).status_code == 200
    kb_id = c.get("/kb/versions").json()["versions"][0]["id"]
    sid = c.post("/schools", json={"name": "概览测试校"}).json()["school_id"]
    r = c.post(
        f"/schools/{sid}/classes",
        json={"name": "七(1)班", "grade": 7, "student_aliases": ["甲", "乙"]},
    ).json()
    return r["class_id"], kb_id


def _bootstrap_no_kb(c: TestClient) -> int:
    """只建 school/class，不导入知识库。返回 class_id。"""
    sid = c.post("/schools", json={"name": "无知识库校"}).json()["school_id"]
    return c.post(
        f"/schools/{sid}/classes",
        json={"name": "七(2)班", "grade": 7, "student_aliases": ["甲"]},
    ).json()["class_id"]


def _student_id(c: TestClient, class_id: int) -> int:
    return c.get(f"/classes/{class_id}/students").json()["students"][0]["student_id"]


def _make_exam(c: TestClient, class_id: int, student_id: int, commit=True, name="概览卷") -> int:
    """建单题考试 + 手工作答；commit=True 提交（已提交），否则留待审核。"""
    exam_id = c.post(
        "/exams",
        json={
            "kb_version_id": 1,
            "class_id": class_id,
            "name": name,
            "exam_date": "2025-11-01",
            "type": "单元",
            "questions": [
                {
                    "idx": 1,
                    "stem": "q1",
                    "q_type": "解答",
                    "full_score": 10,
                    "cog_level": "应用",
                    "kps": [{"code": "M7A-105", "weight": 1.0}],
                }
            ],
        },
    ).json()["exam_id"]
    c.post(f"/exams/{exam_id}/manual", json={"student_id": student_id, "scores": {"1": 3}})
    if commit:
        c.post(f"/exams/{exam_id}/commit")
    return exam_id


# ---------------------------------------------------------------------------
# /classes/overview
# ---------------------------------------------------------------------------


def test_overview_empty(client):
    c, _ = client
    r = c.get("/classes/overview")
    assert r.status_code == 200
    assert r.json() == {"classes": []}


def test_overview_no_exam(client):
    c, _ = client
    class_id, _ = _bootstrap(c)
    row = c.get("/classes/overview").json()["classes"][0]
    assert row["class_id"] == class_id
    assert row["student_count"] == 2
    assert row["exam_count"] == 0
    assert row["todo_count"] == 0
    assert row["latest_exam"] is None
    assert row["progress"]["total"] > 0
    assert row["progress"]["taught"] == 0


def test_overview_progress(client):
    c, _ = client
    class_id, _ = _bootstrap(c)
    c.post(
        f"/classes/{class_id}/progress",
        json={"kp_codes": ["M7A-101", "M7A-102"], "taught_at": "2025-09-10"},
    )
    row = c.get("/classes/overview").json()["classes"][0]
    assert row["progress"]["taught"] == 2
    assert 0 < row["progress"]["taught"] <= row["progress"]["total"]


def test_overview_latest_exam(client):
    c, _ = client
    class_id, _ = _bootstrap(c)
    _make_exam(c, class_id, _student_id(c, class_id), commit=True)
    row = c.get("/classes/overview").json()["classes"][0]
    assert row["exam_count"] == 1
    le = row["latest_exam"]
    assert le is not None
    assert le["name"] == "概览卷"
    assert le["exam_date"] == "2025-11-01"
    assert le["type"] == "单元"
    assert le["submitted"] == 1
    assert le["pending"] == 0


def test_overview_todo(client):
    """有待提交卷（待审核）的考试计入 todo_count。"""
    c, _ = client
    class_id, _ = _bootstrap(c)
    _make_exam(c, class_id, _student_id(c, class_id), commit=False)
    row = c.get("/classes/overview").json()["classes"][0]
    assert row["todo_count"] == 1
    assert row["latest_exam"]["pending"] == 1


def test_overview_no_kb_safe(client):
    """未导入知识库时 progress={0,0}，不崩。"""
    c, _ = client
    class_id = _bootstrap_no_kb(c)
    r = c.get("/classes/overview")
    assert r.status_code == 200
    row = r.json()["classes"][0]
    assert row["class_id"] == class_id
    assert row["progress"] == {"taught": 0, "total": 0}
    assert row["latest_exam"] is None


def test_overview_multiple_classes(client):
    c, _ = client
    _bootstrap(c)
    _bootstrap(c)  # 第二个班
    rows = c.get("/classes/overview").json()["classes"]
    assert len(rows) == 2
    assert {r["class_id"] for r in rows} != set()
