"""干预闭环 API 测试（intervention-loop-design §5 七端点）。

TestClient 走真实路由（tmp 文件库隔离模式，同 test_inbox/test_api_queries）：
端点形状、confirm/skip 状态机（suggested→done|skipped、终态拒绝）、
summary 度量口径、班级诊断单 actions/intervention_summary 占位接通。
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import db as dbmod
from app.api import deps as deps_mod
from app.db import Base
from app.main import app
from app.models import (
    Class,
    ExamResponse,
    ExamTemplate,
    Intervention,
    KbVersion,
    KnowledgePoint,
    ResponseAnswer,
    School,
    Student,
    TeachingProgress,
)

_ENGINE = None


@pytest.fixture()
def client(tmp_path):
    """tmp 文件库 + 全局引擎替换（TestClient 跨线程要求文件库）。"""
    global _ENGINE
    db_file = tmp_path / "iv-api.db"
    eng = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False, autoflush=False)

    old = (dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal)
    dbmod.engine = eng
    dbmod.SessionLocal = S
    deps_mod.SessionLocal = S
    with TestClient(app) as c:
        yield c, S
    dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = old


def _seed(S):
    """kb + 班级 + 学生 + 进度 + 一场已提交考试。"""
    s = S()
    kb = KbVersion(subject="数学", textbook_edition="测试版", version="t")
    s.add(kb)
    s.flush()
    kp_ids = {}
    for code in ("P1", "P2", "P3", "U"):
        kp = KnowledgePoint(
            kb_version_id=kb.id, code=code, name=f"{code}知识点", grade=7,
            semester=1, chapter="章", cog_levels_expected=["应用"],
            difficulty_prior=0.5, mastery_floor=0.6,
        )
        s.add(kp)
        s.flush()
        kp_ids[code] = kp.id
    school = School(name="测试学校")
    s.add(school)
    s.flush()
    clazz = Class(school_id=school.id, name="七(1)班", grade=7)
    s.add(clazz)
    s.flush()
    students = {}
    for i in range(1, 7):
        stu = Student(school_id=school.id, class_id=clazz.id,
                      name_or_alias=f"学生{i:02d}")
        s.add(stu)
        s.flush()
        students[f"T{i:02d}"] = stu.id
    s.add(TeachingProgress(class_id=clazz.id, kp_id=kp_ids["P1"],
                           taught_at=date(2025, 9, 1)))
    # 两场考试，3/6 人弱 → 共性成立 → 提交后有干预行
    for i in range(2):
        tpl = ExamTemplate(class_id=clazz.id, name=f"单元测{i}",
                           exam_date=date(2025, 10, 5 + i * 10), type="单元")
        s.add(tpl)
        s.flush()
        from app.models import QuestionKp, TemplateQuestion
        tq = TemplateQuestion(exam_template_id=tpl.id, idx=1, stem="题",
                              q_type="解答", full_score=10.0, cog_level="应用")
        s.add(tq)
        s.flush()
        s.add(QuestionKp(template_question_id=tq.id, kp_id=kp_ids["P1"], weight=1.0))
        s.flush()
        for name, sid in students.items():
            score = 3.0 if name in ("T01", "T02", "T03") else 9.0
            resp = ExamResponse(exam_template_id=tpl.id, student_id=sid,
                                source="excel", status="待审核")
            s.add(resp)
            s.flush()
            s.add(ResponseAnswer(exam_response_id=resp.id,
                                 template_question_id=tq.id, score=score))
            resp.total_score = score
    from app.ingestion.commit import commit_exam
    for tpl in s.query(ExamTemplate).all():
        commit_exam(s, tpl.id)
    from app.reports.auto_generate import generate_exam_reports
    generate_exam_reports(s, max(t.id for t in s.query(ExamTemplate).all()))
    s.commit()
    out = {"class_id": clazz.id, "students": students, "kp": kp_ids}
    s.close()
    return out


def test_seven_endpoints_shapes_and_state_machine(client):
    """7 端点形状 + confirm/skip 状态机全链路。"""
    c, S = client
    env = _seed(S)
    cid = env["class_id"]

    # 1) action-plan（班）
    r = c.get(f"/classes/{cid}/action-plan")
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["class_id"] == cid
    assert plan["rows"], "共性薄弱应有行动行"
    scopes = [row["scope"] for row in plan["rows"]]
    order = {"class": 0, "group": 1, "student": 2}
    assert [order[x] for x in scopes] == sorted(order[x] for x in scopes), "三层杠杆序"
    target = next(row for row in plan["rows"] if row["status"] == "suggested")

    # 2) interventions 列表 + 过滤
    r = c.get("/interventions", params={"class_id": cid, "status": "suggested"})
    assert r.status_code == 200
    listing = r.json()
    assert listing["total"] >= 1
    assert all(i["status"] == "suggested" for i in listing["items"])
    assert any(i["kind"] == "reteach" for i in listing["items"])
    bad = c.get("/interventions", params={"class_id": cid, "status": "nope"})
    assert bad.status_code == 400

    # 3) confirm
    r = c.post(f"/interventions/{target['id']}/confirm", json=None)
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert r.json()["done_at"]
    again = c.post(f"/interventions/{target['id']}/skip", json={"note": ""})
    assert again.status_code == 400, "done 后不能再 skip"

    # 4) effect（刚确认、无复测证据）
    r = c.get(f"/interventions/{target['id']}/effect")
    assert r.status_code == 200
    eff = r.json()
    assert eff["effect_status"] == "awaiting_retest"
    missing = c.get("/interventions/999999/effect")
    assert missing.status_code == 404

    # 5) skip 另一条（U 点造一条 T04 的个体建议：仅 T04 弱 → 无共性稀释）
    s = S()
    s.add(TeachingProgress(class_id=env["class_id"], kp_id=env["kp"]["U"],
                           taught_at=date(2025, 9, 1)))
    tpl = ExamTemplate(class_id=env["class_id"], name="U点测",
                       exam_date=date(2025, 10, 30), type="单元")
    s.add(tpl)
    s.flush()
    from app.models import QuestionKp, TemplateQuestion
    tq = TemplateQuestion(exam_template_id=tpl.id, idx=1, stem="题",
                          q_type="解答", full_score=10.0, cog_level="应用")
    s.add(tq)
    s.flush()
    s.add(QuestionKp(template_question_id=tq.id, kp_id=env["kp"]["U"], weight=1.0))
    s.flush()
    for name, sid in env["students"].items():
        score = 3.0 if name == "T04" else 9.0
        resp = ExamResponse(exam_template_id=tpl.id, student_id=sid,
                            source="excel", status="待审核")
        s.add(resp)
        s.flush()
        s.add(ResponseAnswer(exam_response_id=resp.id,
                             template_question_id=tq.id, score=score))
        resp.total_score = score
    from app.ingestion.commit import commit_exam
    from app.reports.auto_generate import generate_exam_reports
    commit_exam(s, tpl.id)
    generate_exam_reports(s, tpl.id)
    s.commit()
    s.close()

    r = c.get("/interventions", params={
        "class_id": cid, "student_id": env["students"]["T04"],
        "status": "suggested"})
    items = r.json()["items"]
    assert items, "T04 在 U 点应有个体建议"
    r = c.post(f"/interventions/{items[0]['id']}/skip", json={"note": "下学期再说"})
    assert r.status_code == 200 and r.json()["status"] == "skipped"

    # 6) summary（北极星口径）
    r = c.get("/interventions/summary", params={"class_id": cid})
    assert r.status_code == 200
    summ = r.json()
    assert summ["by_status"]["done"] >= 1
    assert summ["by_status"]["skipped"] >= 1
    assert summ["adoption_rate"] is not None
    # awaiting 主导时提升率为 None（可评估子集为空），不抛零除
    if summ["evaluable_count"] == 0:
        assert summ["intervention_lift_rate"] is None

    # 7) 学生改进单 get-or-generate
    sid = env["students"]["T01"]
    r = c.get(f"/students/{sid}/action-plan")
    assert r.status_code == 200
    body = r.json()
    assert body["report_id"] and body["markdown"].startswith("# ")
    # 再取一次应命中同一份（get 不再生成）
    rid_first = body["report_id"]
    r2 = c.get(f"/students/{sid}/action-plan")
    assert r2.json()["report_id"] == rid_first
    ghost = c.get("/students/99999/action-plan")
    assert ghost.status_code == 404


def test_action_plan_rows_carry_loop_state(client):
    """闭环一期 P1：action-plan 行带 loop_state（suggested → 已建议）与
    retest_exam_id 字段；学生行折叠、集体行三态同源。"""
    c, S = client
    env = _seed(S)
    r = c.get(f"/classes/{env['class_id']}/action-plan")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert rows, "共性薄弱应产出建议行"
    assert all("loop_state" in row for row in rows)
    assert all("retest_exam_id" in row for row in rows)
    assert any(row["loop_state"] == "已建议" for row in rows if row["status"] == "suggested")


def test_diagnosis_sheet_actions_wired(client):
    """班级诊断单 actions/intervention_summary 占位接通（替换空结构）。"""
    c, S = client
    env = _seed(S)
    cid = env["class_id"]

    r = c.get(f"/classes/{cid}/diagnosis-sheet")
    assert r.status_code == 200, r.text
    sheet = r.json()
    assert sheet["actions"]["pending_confirm"] >= 1, "占位 0 应被真实计数替换"
    assert sheet["actions"]["rows"], "行动明细应有行"
    row = sheet["actions"]["rows"][0]
    assert {"id", "kind", "scope", "status", "kp_name"} <= set(row)
    summ = sheet["intervention_summary"]
    assert isinstance(summ, dict) and "intervention_lift_rate" in summ
    assert summ["by_status"]["suggested"] >= 0


def test_action_plan_class_not_found(client):
    c, _ = client
    assert c.get("/classes/424242/action-plan").status_code == 404
    assert c.get("/interventions/summary", params={"class_id": 424242}).status_code == 404


def test_group_batch_confirm_and_skip(client):
    """队列按组折叠展示；with_group=true 批量落事实（操作层一次、事实层逐行）。"""
    from datetime import datetime

    c, S = client
    env = _seed(S)
    cid = env["class_id"]

    # U 点单题考试 → 全员 gate=数据不足 → 折叠态「证据不足」∈ 队列白名单
    # （需先有教学进度，否则 gate=未学到，不在队列白名单）
    s = S()
    s.add(TeachingProgress(class_id=cid, kp_id=env["kp"]["U"],
                           taught_at=date(2025, 9, 1)))
    tpl = ExamTemplate(class_id=cid, name="U单证据", exam_date=date(2025, 11, 1),
                       type="单元")
    s.add(tpl)
    s.flush()
    from app.models import QuestionKp, TemplateQuestion
    tq = TemplateQuestion(exam_template_id=tpl.id, idx=1, stem="题",
                          q_type="解答", full_score=10.0, cog_level="应用")
    s.add(tq)
    s.flush()
    s.add(QuestionKp(template_question_id=tq.id, kp_id=env["kp"]["U"], weight=1.0))
    s.flush()
    for name, sid in env["students"].items():
        resp = ExamResponse(exam_template_id=tpl.id, student_id=sid,
                            source="excel", status="待审核")
        s.add(resp)
        s.flush()
        s.add(ResponseAnswer(exam_response_id=resp.id,
                             template_question_id=tq.id, score=5.0))
        resp.total_score = 5.0
    from app.ingestion.commit import commit_exam
    commit_exam(s, tpl.id)
    # 同组三行（模拟小组聚类产物）
    for name in ("T01", "T02", "T03"):
        s.add(Intervention(
            class_id=cid, student_id=env["students"][name], kp_id=env["kp"]["U"],
            exam_id=tpl.id, kind="spaced_review", scope="group",
            group_ref="r1:U", baseline_as_of=datetime(2025, 10, 1, 12, 0),
            status="suggested",
        ))
    s.commit()
    s.close()

    r = c.get(f"/classes/{cid}/action-plan")
    assert r.status_code == 200
    grp = [x for x in r.json()["rows"] if x["scope"] == "group"]
    assert len(grp) == 1, "同组折叠成一行"
    assert grp[0]["group_size"] == 3 and grp[0]["group_ref"] == "r1:U"

    rep = grp[0]["id"]
    r = c.post(f"/interventions/{rep}/confirm",
               params={"with_group": True}, json=None)
    assert r.status_code == 200 and r.json()["confirmed"] == 3
    s = S()
    assert s.query(Intervention).filter_by(
        group_ref="r1:U", status="done").count() == 3, "同组三行各自落事实"
    s.close()

    # 再造一组走 skip 批量
    s = S()
    for name in ("T04", "T05", "T06"):
        s.add(Intervention(
            class_id=cid, student_id=env["students"][name], kp_id=env["kp"]["U"],
            exam_id=tpl.id, kind="spaced_review", scope="group",
            group_ref="r2:U", baseline_as_of=datetime(2025, 10, 1, 12, 0),
            status="suggested",
        ))
    s.commit()
    s.close()
    r = c.get(f"/classes/{cid}/action-plan")
    grp2 = next(x for x in r.json()["rows"]
                if x.get("group_ref") == "r2:U")
    r = c.post(f"/interventions/{grp2['id']}/skip",
               params={"with_group": True}, json={"note": "下学期"})
    assert r.status_code == 200 and r.json()["skipped"] == 3

    # 普通行（无 group_ref）with_group 不扩散：confirmed==1
    r = c.get("/interventions", params={"class_id": cid, "status": "suggested"})
    solo = next(x for x in r.json()["items"] if x["scope"] != "group")
    r = c.post(f"/interventions/{solo['id']}/confirm",
               params={"with_group": True}, json=None)
    assert r.status_code == 200 and r.json()["confirmed"] == 1
