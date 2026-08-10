"""提交后自动生成报告（docs/auto-report-on-commit-plan.md §1.5）测试。

覆盖：
- 提交一场考试 → 生成 1 份班级质量报告 + N 份已参加学生诊断（均带 exam_id）
- 重复提交/补录再提交 → 按 (exam_id, type) 替换，不产生重复行
- 查看端点 get-or-generate：有已存报告直接返回，不再新建行
- AI 解读首次生成后缓存（第二次不再调 LLM）
- 无参数 diagnosis 返回最近一场考试的已存诊断
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.db import Base
from app.ingestion.commit import add_manual_response, commit_exam
from app.models import Report
from app.reports.auto_generate import generate_exam_reports
from tests.conftest import add_progress, make_exam


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    s = S()
    yield s
    s.close()


def _commit(session, env, first_n=None, day=date(2025, 10, 10)):
    """全班（或前 first_n 人）手动录入一场考试并提交，返回 (tpl, 已录学生 id 列表)。

    候选4：commit_exam 不再生成报告，此处显式组合 generate_exam_reports（与 API
    commit 端点同构，测试端点级语义「提交即自动生成」）。
    """
    env["kb"].status = "active"
    session.flush()
    kp = env["kp"]["P1"]
    tpl = make_exam(
        session, env["class"].id, "自动生成月考", day, "单元",
        [(1, 10.0, "解答", "应用", [(kp, 1.0)]),
         (2, 5.0, "选择", "识记", [(kp, 1.0)])],
    )
    add_progress(session, env["class"].id, [kp])
    ids = list(env["students"].values())
    picked = ids if first_n is None else ids[:first_n]
    for i, sid in enumerate(picked):
        add_manual_response(session, tpl.id, sid, {1: 10.0 - i, 2: 5.0})
    result = commit_exam(session, tpl.id)
    if result.committed_responses > 0:
        reports = generate_exam_reports(session, tpl.id)
        result.quality_report = reports.quality
        result.diagnoses = reports.diagnoses
    session.flush()
    return tpl, picked, result


def test_commit_generates_quality_and_diagnoses(session, env):
    """提交后自动生成 1 份班级质量报告 + 全班每生 1 份诊断，均关联 exam_id。"""
    tpl, ids, result = _commit(session, env)
    assert result.quality_report is True
    assert result.diagnoses == len(ids)

    quality = session.scalars(
        select(Report).where(Report.type == "quality_analysis")
    ).all()
    assert len(quality) == 1
    assert quality[0].exam_id == tpl.id
    assert "考后质量分析" in quality[0].content_markdown

    diags = session.scalars(
        select(Report).where(Report.type == "student_diagnosis")
    ).all()
    assert len(diags) == len(ids)
    assert {d.student_id for d in diags} == set(ids)
    assert all(d.exam_id == tpl.id for d in diags)
    assert all(d.content_markdown for d in diags)


def test_commit_twice_no_duplicates(session, env):
    """全部已提交后再次提交：不产生新作答，也不重复生成报告。"""
    tpl, _, _ = _commit(session, env)
    before = len(session.scalars(select(Report)).all())
    result2 = commit_exam(session, tpl.id)
    session.flush()
    assert result2.committed_responses == 0
    assert len(session.scalars(select(Report)).all()) == before


def test_commit_regenerates_replacing_old(session, env):
    """部分提交后再补录提交：报告整体替换（无重复行），覆盖全部已提交学生。"""
    tpl, picked, _ = _commit(session, env, first_n=3)
    assert len(session.scalars(
        select(Report).where(Report.type == "student_diagnosis")
    ).all()) == 3

    ids = list(env["students"].values())
    for i, sid in enumerate(ids[3:]):
        add_manual_response(session, tpl.id, sid, {1: 6.0 - i, 2: 3.0})
    result = commit_exam(session, tpl.id)
    if result.committed_responses > 0:
        reports = generate_exam_reports(session, tpl.id)
        result.quality_report = reports.quality
        result.diagnoses = reports.diagnoses
    session.flush()

    assert result.diagnoses == len(ids)
    diags = session.scalars(
        select(Report).where(Report.type == "student_diagnosis")
    ).all()
    assert len(diags) == len(ids), "补录后应整体替换为全部学生，无重复行"
    assert {d.student_id for d in diags} == set(ids)
    assert len(session.scalars(
        select(Report).where(Report.type == "quality_analysis")
    ).all()) == 1


def test_commit_no_kb_skips_reports(session, env):
    """库中完全没有知识库版本 → 报告生成跳过（best-effort），不影响提交。"""
    from app.models import KbVersion

    session.query(KbVersion).delete()
    session.flush()
    kp = env["kp"]["P1"]
    tpl = make_exam(
        session, env["class"].id, "无知识库月考", date(2025, 10, 10), "单元",
        [(1, 10.0, "解答", "应用", [(kp, 1.0)])],
    )
    add_manual_response(session, tpl.id, env["students"]["T01"], {1: 8.0})
    result = commit_exam(session, tpl.id)
    session.flush()
    assert result.committed_responses == 1
    assert result.quality_report is False
    assert result.diagnoses == 0
    assert session.scalar(select(Report).where(Report.exam_id == tpl.id)) is None


# ---------------------------------------------------------------------------
# 查看端点 get-or-generate + 解读缓存（TestClient，临时库隔离）
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "auto_report_test.db"
    import app.api.routes as routes_mod
    import app.api.deps as deps_mod
    import app.db as dbmod
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from fastapi.testclient import TestClient
    from app.main import app

    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    new_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    original = (dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal)
    dbmod.engine, dbmod.SessionLocal = engine, new_session
    deps_mod.SessionLocal = new_session
    with TestClient(app) as c:
        yield c, new_session
    dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = original


def _bootstrap_api(client):
    """走真实 /kb/import（draft，API 层兜底可用）+ 学校/班级/学生。"""
    from pathlib import Path

    kb_yaml = (
        Path(__file__).resolve().parents[1] / "kb" / "math" / "grade7" / "kb.yaml"
    )
    assert client.post("/kb/import", json={"yaml_path": str(kb_yaml)}).status_code == 200
    sid = client.post("/schools", json={"name": "自动报告校"}).json()["school_id"]
    r = client.post(
        f"/schools/{sid}/classes",
        json={"name": "七(10)班", "grade": 7, "student_aliases": ["甲", "乙"]},
    ).json()
    return r["class_id"], r["student_ids"]


def _commit_via_api(client, class_id, student_ids, scores=(5, 4, 3, 2)):
    exam_id = client.post(
        "/exams",
        json={
            "kb_version_id": 1,
            "class_id": class_id,
            "name": "自动报告卷",
            "exam_date": "2025-11-02",
            "type": "单元",
            "questions": [
                {"idx": 1, "stem": "q1", "q_type": "选择", "full_score": 5,
                 "cog_level": "理解", "n_options": 4,
                 "kps": [{"code": "M7A-105", "weight": 1.0}]},
            ],
        },
    ).json()["exam_id"]
    for i, sid in enumerate(student_ids):
        assert client.post(
            f"/exams/{exam_id}/manual",
            json={"student_id": sid, "scores": {"1": scores[i]}},
        ).status_code == 200
    r = client.post(f"/exams/{exam_id}/commit").json()
    assert r["quality_report"] is True and r["diagnoses"] == len(student_ids)
    return exam_id


def test_quality_report_get_or_generate(client):
    """已存报告直接返回（不新建行）；narrative 首次生成后缓存。"""
    c, S = client
    class_id, student_ids = _bootstrap_api(c)
    exam_id = _commit_via_api(c, class_id, student_ids)

    from app.llm.client import MockLLMClient, set_client

    mock = MockLLMClient([{"text": "班级整体在绝对值上需关注。"}])
    set_client(mock)
    try:
        r1 = c.get(f"/classes/{class_id}/quality-report?exam_id={exam_id}&narrative=true").json()
        assert "AI 解读" in r1["markdown"]
        r2 = c.get(f"/classes/{class_id}/quality-report?exam_id={exam_id}&narrative=true").json()
    finally:
        set_client(None)

    assert r1["report_id"] == r2["report_id"], "第二次查看应命中同一份已存报告"
    with S() as s:
        rows = s.scalars(
            select(Report).where(
                Report.exam_id == exam_id, Report.type == "quality_analysis"
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].narrative_markdown and "AI 解读" in rows[0].narrative_markdown
    assert len(mock.calls) == 1, "解读已缓存，第二次不再调 LLM"


def test_diagnosis_latest_stored_default(client):
    """无 exam_id/as_of：返回最近一场考试的已存诊断（不新建、不现算）。"""
    c, S = client
    class_id, student_ids = _bootstrap_api(c)
    exam_id = _commit_via_api(c, class_id, student_ids)

    r = c.get(f"/students/{student_ids[0]}/diagnosis").json()
    assert "学习诊断单" in r["markdown"]
    with S() as s:
        exam_linked = s.scalars(
            select(Report).where(
                Report.student_id == student_ids[0],
                Report.type == "student_diagnosis",
                Report.exam_id.isnot(None),
            )
        ).all()
        assert len(exam_linked) == 1 and exam_linked[0].exam_id == exam_id
        # 该生仅这一份关联考试的诊断 → 默认展示的就是它（无额外新建行）
        assert len(s.scalars(
            select(Report).where(Report.student_id == student_ids[0])
        ).all()) == 1


def test_diagnosis_exam_param_returns_stored(client):
    """指定 exam_id 时返回该场已存诊断；无考试则 404。"""
    c, _ = client
    class_id, student_ids = _bootstrap_api(c)
    exam_id = _commit_via_api(c, class_id, student_ids)

    r = c.get(f"/students/{student_ids[0]}/diagnosis?exam_id={exam_id}").json()
    assert "学习诊断单" in r["markdown"]
    assert r["as_of"] == "2025-11-02", "快照 as_of 应等于考试日，供前端标注与右侧面板同步"
    assert c.get(f"/students/{student_ids[0]}/diagnosis?exam_id=9999").status_code == 404
