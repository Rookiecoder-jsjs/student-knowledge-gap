"""前端交互补齐端点：列表回读 / 逐题改标 / 得分修正 / 教师否决 / KB 上传 / 报告回读。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

KB_YAML = Path(__file__).resolve().parents[1] / "kb" / "math" / "grade7" / "kb.yaml"

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path):
    """临时库隔离（替换 app.db 与 deps 两处 SessionLocal；候选2 拆路由后 routes 无依赖）。"""
    db_path = tmp_path / "queries_test.db"
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


def _bootstrap(client: TestClient) -> tuple[int, list[int]]:
    assert client.post("/kb/import", json={"yaml_path": str(KB_YAML)}).status_code == 200
    sid = client.post("/schools", json={"name": "查询测试校"}).json()["school_id"]
    r = client.post(
        f"/schools/{sid}/classes",
        json={"name": "七(9)班", "grade": 7, "student_aliases": ["甲同学", "乙同学"]},
    ).json()
    return r["class_id"], r["student_ids"]


def _create_exam(client: TestClient, class_id: int, name: str = "查询测试卷") -> int:
    r = client.post(
        "/exams",
        json={
            "kb_version_id": 1,
            "class_id": class_id,
            "name": name,
            "exam_date": "2025-11-01",
            "type": "单元",
            "questions": [
                {"idx": 1, "stem": "q1", "q_type": "选择", "full_score": 5,
                 "cog_level": "理解", "n_options": 4,
                 "kps": [{"code": "M7A-105", "weight": 1.0}]},
                {"idx": 2, "stem": "q2", "q_type": "解答", "full_score": 10,
                 "kps": [{"code": "M7A-111", "weight": 1.0}]},
            ],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["exam_id"]


# ---------------------------------------------------------------------------
# P0 列表与回读
# ---------------------------------------------------------------------------


def test_list_classes_students_progress(client):
    c, _ = client
    class_id, student_ids = _bootstrap(c)

    classes = c.get("/classes").json()["classes"]
    assert len(classes) == 1
    assert classes[0]["student_count"] == 2 and classes[0]["exam_count"] == 0

    stu = c.get(f"/classes/{class_id}/students").json()["students"]
    assert [s["student_id"] for s in stu] == student_ids  # 名单原序
    assert all("score" not in s for s in stu)  # 列表接口不得携带分数字段

    assert c.get("/classes/999/students").status_code == 404

    assert c.post(
        f"/classes/{class_id}/progress",
        json={"kp_codes": ["M7A-101", "M7A-102"], "taught_at": "2025-09-10"},
    ).json()["added"] == 2
    prog = c.get(f"/classes/{class_id}/progress").json()["progress"]
    assert {p["code"] for p in prog} == {"M7A-101", "M7A-102"}


def test_exam_list_detail_responses_matrix(client):
    c, _ = client
    class_id, student_ids = _bootstrap(c)
    exam_id = _create_exam(c, class_id)

    exams = c.get(f"/exams?class_id={class_id}").json()["exams"]
    assert len(exams) == 1
    assert exams[0]["question_count"] == 2
    # 手工建卷的标注视为已审核，不进审核台
    assert exams[0]["unreviewed_tags"] == 0

    detail = c.get(f"/exams/{exam_id}").json()
    assert [q["idx"] for q in detail["questions"]] == [1, 2]
    assert detail["questions"][0]["kps"][0]["code"] == "M7A-105"
    assert detail["questions"][0]["kps"][0]["reviewed"] is True

    # 采集矩阵：一个已录、一个未采集
    assert c.post(
        f"/exams/{exam_id}/manual",
        json={"student_id": student_ids[0], "scores": {"1": 5, "2": 4}},
    ).status_code == 200
    matrix = c.get(f"/exams/{exam_id}/responses").json()
    assert matrix["summary"] == {"未采集": 1, "待审核": 1, "已提交": 0}
    by_sid = {r["student_id"]: r for r in matrix["responses"]}
    assert by_sid[student_ids[0]]["status"] == "待审核"
    assert by_sid[student_ids[1]]["status"] == "未采集"

    c.post(f"/exams/{exam_id}/commit")
    assert c.get(f"/exams/{exam_id}/responses").json()["summary"]["已提交"] == 1


# ---------------------------------------------------------------------------
# P1 审核台修正
# ---------------------------------------------------------------------------


def test_patch_question_tags(client):
    c, _ = client
    class_id, _ = _bootstrap(c)
    exam_id = _create_exam(c, class_id)
    qid = c.get(f"/exams/{exam_id}").json()["questions"][0]["question_id"]

    # 闭集校验
    bad = c.patch(f"/template-questions/{qid}/tags", json={"kps": [{"code": "FAKE-1"}]})
    assert bad.status_code == 400

    # 正常改标：替换为两个知识点，改后即已审核
    r = c.patch(
        f"/template-questions/{qid}/tags",
        json={"kps": [{"code": "M7A-104", "weight": 0.6}, {"code": "M7A-105", "weight": 0.4}]},
    )
    assert r.status_code == 200 and sorted(r.json()["kps"]) == ["M7A-104", "M7A-105"]
    kps = c.get(f"/exams/{exam_id}").json()["questions"][0]["kps"]
    assert len(kps) == 2 and all(k["source"] == "教师" and k["reviewed"] for k in kps)

    # 提交后禁止改标（会令已派生证据失效）
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import Student

    with SessionLocal() as s:
        sid = s.scalar(select(Student.id))
    c.post(f"/exams/{exam_id}/manual", json={"student_id": sid, "scores": {"1": 5, "2": 10}})
    c.post(f"/exams/{exam_id}/commit")
    assert c.patch(
        f"/template-questions/{qid}/tags", json={"kps": [{"code": "M7A-105"}]}
    ).status_code == 400


def test_patch_answer_score(client):
    c, session_factory = client
    class_id, student_ids = _bootstrap(c)
    exam_id = _create_exam(c, class_id)
    c.post(
        f"/exams/{exam_id}/manual",
        json={"student_id": student_ids[0], "scores": {"1": 5, "2": 4}},
    )

    from sqlalchemy import select
    from app.models import ResponseAnswer

    with session_factory() as s:
        ans = s.scalar(select(ResponseAnswer).where(ResponseAnswer.score == 4))
        answer_id = ans.id

    # 越界拒绝
    assert c.patch(f"/response-answers/{answer_id}", json={"score": 99}).status_code == 400
    # 正常修正：总分随之重算
    r = c.patch(f"/response-answers/{answer_id}", json={"score": 9})
    assert r.status_code == 200
    assert r.json()["score"] == 9 and r.json()["total_score"] == 14

    # 提交后锁定
    c.post(f"/exams/{exam_id}/commit")
    assert c.patch(f"/response-answers/{answer_id}", json={"score": 8}).status_code == 400


def test_override_attribution_not_revived(client):
    c, session_factory = client
    class_id, student_ids = _bootstrap(c)

    from app.models import Attribution

    with session_factory() as s:
        att = Attribution(student_id=student_ids[0], kp_id=1, type="前置缺陷",
                          confidence=0.8, status="active")
        s.add(att)
        s.commit()
        att_id = att.id

    r = c.post(f"/attributions/{att_id}/override", json={"note": "该生课前已自学"})
    assert r.status_code == 200 and r.json()["status"] == "overridden"
    # 重复否决拒绝
    assert c.post(f"/attributions/{att_id}/override", json={"note": ""}).status_code == 400

    # 引擎重跑不得复活被否决的归因（教师否决权）
    c.post(f"/students/{student_ids[0]}/attributions")
    with session_factory() as s:
        db_att = s.get(Attribution, att_id)
        assert db_att.status == "overridden"  # 重跑后仍是 overridden
        assert db_att.teacher_note == "该生课前已自学"


def test_attributions_closure_endpoint(client):
    """回归：端点曾与导入的领域函数 attribution_closure 同名——def shadow 导入后
    `return attribution_closure(...)` 变自调用 → RecursionError → 500（活体冒烟发现）。
    """
    c, session_factory = client
    class_id, student_ids = _bootstrap(c)

    from app.models import Attribution

    with session_factory() as s:
        s.add(Attribution(student_id=student_ids[0], kp_id=1, type="前置缺陷",
                          confidence=0.8, status="active"))
        s.commit()

    r = c.get("/attributions/closure", params={"class_id": class_id})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 1
    assert data["by_status"]["active"] == 1
    assert data["closure_rate"] == 0.0  # 尚未经诊断题验证

    # 无 class_id 的全局分支同样可达
    assert c.get("/attributions/closure").status_code == 200


# ---------------------------------------------------------------------------
# P2 知识库上传与报告回读
# ---------------------------------------------------------------------------


def test_kb_upload(client):
    c, _ = client
    _bootstrap(c)  # 已导入 kb_version_id=1
    r = c.post("/kb/upload", files={"file": ("kb.yaml", KB_YAML.read_bytes(), "text/yaml")})
    assert r.status_code == 200, r.text
    # 同内容重复导入幂等：仍返回版本 1，不分裂出新版本
    assert r.json()["kb_version_id"] == 1

    bad = c.post("/kb/upload", files={"file": ("bad.yaml", b"not: [valid", "text/yaml")})
    assert bad.status_code == 400


def test_kb_import_idempotent(client):
    """同一 YAML 重复导入不产生第二个 kb_version（防止分析断链）。"""
    c, _ = client
    r1 = c.post("/kb/import", json={"yaml_path": str(KB_YAML)}).json()
    r2 = c.post("/kb/import", json={"yaml_path": str(KB_YAML)}).json()
    assert r1["kb_version_id"] == r2["kb_version_id"]


def test_reports_list_and_detail(client):
    c, session_factory = client
    class_id, _ = _bootstrap(c)

    from app.models import Report

    with session_factory() as s:
        s.add(Report(type="quality_analysis", class_id=class_id,
                     content_markdown="# 测试报告", snapshot_json={"k": 1}))
        s.commit()

    reports = c.get("/reports").json()["reports"]
    assert len(reports) == 1 and reports[0]["type"] == "quality_analysis"
    detail = c.get(f"/reports/{reports[0]['report_id']}").json()
    assert detail["markdown"] == "# 测试报告"
    assert detail["snapshot"] == {"k": 1}
    assert c.get("/reports?class_id=999").json()["reports"] == []
    assert c.get("/reports/999").status_code == 404
