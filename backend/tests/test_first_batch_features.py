"""第一批功能：质量闸门、证据隔离、行动卡与持久复测计划。"""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import inspect, select, text

from app import auth
from app.ingestion.commit import add_manual_response, commit_exam
from app.ingestion.preflight import exam_preflight
from app.models import (
    EvidenceEvent, ExamResponse, Intervention, QuestionKp, ResponseAnswer,
    Student, Teacher, TemplateQuestion,
)
from app.retest import retest_progress
from tests.test_api_queries import client, _bootstrap, _create_exam  # noqa: F401


@pytest.fixture()
def feature_env(client):
    c, factory = client
    cid, students = _bootstrap(c)
    eid = _create_exam(c, cid)
    with factory() as db:
        response = add_manual_response(db, eid, students[0], {1: 2, 2: 4})
        kp_id = db.scalar(select(QuestionKp.kp_id).join(TemplateQuestion).where(
            TemplateQuestion.exam_template_id == eid, TemplateQuestion.idx == 1))
        iv = Intervention(class_id=cid, student_id=students[0], kp_id=kp_id,
                          exam_id=eid, kind="reteach", scope="student",
                          baseline_as_of=datetime(2025, 11, 1))
        db.add(iv)
        db.commit()
        return {"client": c, "factory": factory, "class": cid, "students": students,
                "exam": eid, "response": response.id, "kp": kp_id, "iv": iv.id}


def test_preflight_blocks_missing_and_invalid_scores_and_can_repair(feature_env):
    e = feature_env
    with e["factory"]() as db:
        response = db.get(ExamResponse, e["response"])
        db.delete(response.answers[1])
        response.answers[0].score = 99
        db.commit()
    c = e["client"]
    check = c.get(f"/exams/{e['exam']}/preflight").json()
    assert not check["ready"]
    assert {"missing_answer", "score_out_of_range"} <= {i["code"] for i in check["issues"]}
    assert c.post(f"/exams/{e['exam']}/commit").status_code == 400
    with e["factory"]() as db:
        assert db.get(ExamResponse, e["response"]).status == "待审核"
        assert db.scalar(select(EvidenceEvent.id)) is None
    for idx, score in [(1, 3), (2, 7)]:
        assert c.put(f"/exams/{e['exam']}/responses/{e['response']}/scores/{idx}", json={"score": score}).status_code == 200
    assert c.get(f"/exams/{e['exam']}/preflight").json()["ready"]
    assert c.post(f"/exams/{e['exam']}/commit").status_code == 200
    assert c.put(f"/exams/{e['exam']}/responses/{e['response']}/scores/1", json={"score": 4}).status_code == 400


def test_total_mismatch_and_parsing_are_blocked(feature_env):
    e = feature_env
    with e["factory"]() as db:
        response = db.get(ExamResponse, e["response"])
        response.total_score = 500
        response.status = "解析中"
        db.commit()
    result = e["client"].get(f"/exams/{e['exam']}/preflight").json()
    assert {"not_ready", "total_mismatch"} <= {i["code"] for i in result["issues"] if i["severity"] == "blocking"}


def test_manual_entry_requires_explicit_zero_and_rejects_nan(feature_env):
    e = feature_env
    with e["factory"]() as db:
        for scores in [{1: 0}, {1: float("nan"), 2: 0}, {1: 0, 2: 0, 3: 0}]:
            with pytest.raises(ValueError):
                add_manual_response(db, e["exam"], e["students"][1], scores)
        result = add_manual_response(db, e["exam"], e["students"][1], {1: 0, 2: 0})
        assert result.total_score == 0


def test_excel_quality_warnings_survive_import_and_need_score_review(feature_env, tmp_path):
    from openpyxl import Workbook
    from app.ingestion.excel import import_excel
    e = feature_env
    path = tmp_path / "quality.xlsx"
    wb = Workbook()
    wb.active.append(["姓名", "Q1", "Q2", "总分"])
    wb.active.append(["乙同学", None, 99, 100])
    wb.save(path)
    wb.close()
    with e["factory"]() as db:
        import_excel(db, e["exam"], path)
        db.commit()
    check = e["client"].get(f"/exams/{e['exam']}/preflight").json()
    assert not check["ready"]
    assert any(i["code"] == "source_warning" and "总分" in i["message"] for i in check["issues"])
    assert sum(i["code"] == "low_confidence" and i["severity"] == "blocking" for i in check["issues"]) == 2


def test_excel_duplicate_columns_and_students_remain_visible(feature_env, tmp_path):
    from openpyxl import Workbook
    from app.ingestion.excel import import_excel
    e = feature_env
    path = tmp_path / "duplicates.xlsx"
    wb = Workbook()
    wb.active.append(["姓名", "Q1", "Q1", "Q2"])
    wb.active.append(["乙同学", 3, 5, 4])
    wb.active.append(["乙同学", 2, 5, 4])
    wb.save(path); wb.close()
    with e["factory"]() as db:
        result = import_excel(db, e["exam"], path)
        db.commit()
        assert result.imported == 1
        warnings = [i["message"] for i in exam_preflight(db, e["exam"])["issues"] if i["code"] == "source_warning"]
        assert any("重复得分列" in message for message in warnings)
        assert any("疑似重复行" in message for message in warnings)


@pytest.mark.parametrize("kind", ["reteach", "prereq_backfill", "spaced_review", "contrast_practice", "evidence_boost", "tier_drill"])
def test_teaching_cards_use_real_questions_and_fifteen_minutes(feature_env, kind):
    e = feature_env
    with e["factory"]() as db:
        db.get(Intervention, e["iv"]).kind = kind
        db.commit()
    response = e["client"].get(f"/interventions/{e['iv']}/teaching-card")
    assert response.status_code == 200
    card = response.json()
    assert sum(step["minutes"] for step in card["steps"]) == 15
    assert card["examples"] == [{"exam_id": e["exam"], "exam_name": "查询测试卷", "question_idx": 1, "stem": "q1"}]


def test_confirm_persists_default_date_and_reschedule_validates(feature_env):
    e = feature_env
    c = e["client"]
    result = c.post(f"/interventions/{e['iv']}/confirm").json()
    expected = (datetime.fromisoformat(result["done_at"]).date() + timedelta(days=7)).isoformat()
    assert result["retest_due_date"] == expected
    with e["factory"]() as db:
        assert db.get(Intervention, e["iv"]).retest_due_date.isoformat() == expected
    for days, status in [(-1, 400), (91, 400), (3, 200)]:
        r = c.patch(f"/interventions/{e['iv']}/retest-schedule", json={"retest_due_date": (date.today() + timedelta(days=days)).isoformat()})
        assert r.status_code == status
    schedule = c.get(f"/classes/{e['class']}/retest-schedule").json()
    assert schedule["items"][0]["retest_status"] == "scheduled"
    assert schedule["items"][0]["target_students"] == 1


def test_group_confirmation_and_rescheduling_share_date(feature_env):
    e = feature_env
    with e["factory"]() as db:
        first = db.get(Intervention, e["iv"])
        first.scope, first.group_ref = "group", "test-group"
        other = Intervention(class_id=e["class"], student_id=e["students"][1], kp_id=e["kp"], exam_id=e["exam"],
                             kind="reteach", scope="group", group_ref="test-group", baseline_as_of=first.baseline_as_of)
        db.add(other)
        db.commit()
    c = e["client"]
    due = (date.today() + timedelta(days=2)).isoformat()
    r = c.post(f"/interventions/{e['iv']}/confirm?with_group=true", json={"retest_due_date": due})
    assert r.json()["confirmed"] == 2
    assert c.patch(f"/interventions/{e['iv']}/retest-schedule?with_group=true", json={"retest_due_date": date.today().isoformat()}).json()["updated"] == 2
    with e["factory"]() as db:
        assert {r.retest_due_date for r in db.scalars(select(Intervention))} == {date.today()}


def test_retest_needs_post_action_submitted_evidence(feature_env):
    e = feature_env
    retest_exam = _create_exam(e["client"], e["class"], name="复测卷")
    with e["factory"]() as db:
        iv = db.get(Intervention, e["iv"])
        iv.status, iv.done_at, iv.retest_due_date = "done", datetime(2025, 10, 1), date(2025, 10, 8)
        original = db.get(ExamResponse, e["response"])
        response = add_manual_response(db, retest_exam, e["students"][0], {1: 2, 2: 4})
        ev = EvidenceEvent(student_id=e["students"][0], kp_id=e["kp"], response_answer_id=response.answers[0].id,
                           source_type="单元", value=0.2, weight=1, cog_level="理解",
                           occurred_at=datetime(2025, 11, 1), algo_version="test")
        db.add(ev)
        db.flush()
        assert retest_progress(db, iv)["retest_status"] == "overdue"
        response.status = "已提交"
        db.flush()
        assert retest_progress(db, iv)["retest_status"] == "verified"
        original.status = "已提交"
        answer_id = ev.response_answer_id
        ev.response_answer_id = original.answers[0].id
        db.flush()
        assert retest_progress(db, iv)["retest_status"] == "overdue", "来源考试本身不能充当复测"
        ev.response_answer_id = answer_id
        # 有证据并不表示分数改善；错误知识点、零权重、未来证据不能完成验证。
        for field, value in [("kp_id", e["kp"] + 1), ("weight", 0), ("occurred_at", datetime.now() + timedelta(days=1))]:
            old = getattr(ev, field)
            setattr(ev, field, value)
            db.flush()
            assert retest_progress(db, iv)["retest_status"] == "overdue"
            setattr(ev, field, old)
        ev.occurred_at = iv.done_at
        db.flush()
        assert retest_progress(db, iv)["retest_status"] == "overdue"


def test_alembic_schedule_upgrade_roundtrip(tmp_path):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    eng = create_engine(url)
    assert "retest_due_date" in {c["name"] for c in inspect(eng).get_columns("intervention")}
    eng.dispose()
    command.downgrade(cfg, "f1a2b3c4d5e6")
    command.upgrade(cfg, "head")


def test_evidence_is_cutoff_paginated_and_only_submitted(feature_env):
    e = feature_env
    c = e["client"]
    with e["factory"]() as db:
        commit_exam(db, e["exam"])
        answer = db.get(ExamResponse, e["response"]).answers[0]
        for i in range(12):
            db.add(EvidenceEvent(student_id=e["students"][0], kp_id=e["kp"], response_answer_id=answer.id,
                                 source_type="练习", value=0.2, weight=0.5, cog_level="应用",
                                 occurred_at=datetime(2025, 11, 2) + timedelta(days=i), algo_version="test"))
        db.commit()
    path = f"/students/{e['students'][0]}/knowledge-points/{e['kp']}/evidence"
    early = c.get(path, params={"as_of": "2025-11-01"}).json()
    assert early["total"] == 1
    assert early["items"][0]["score"] == 2
    assert early["items"][0]["full_score"] == 5
    later = c.get(path, params={"as_of": "2025-12-01", "limit": 10}).json()
    assert later["total"] == 13 and later["has_more"]
    assert all(i["effective_weight"] < i["weight"] for i in later["items"])
    assert c.get(path, params={"offset": -1}).status_code == 422
    with e["factory"]() as db:
        db.get(ExamResponse, e["response"]).status = "待审核"
        db.commit()
    assert c.get(path).json()["total"] == 0


def test_new_endpoints_respect_teacher_class_boundary(feature_env):
    e = feature_env
    with e["factory"]() as db:
        student = db.get(Student, e["students"][0])
        teacher = Teacher(school_id=student.school_id, name="无班级教师", username="outsider",
                          salt=b"test-salt", password_hash=b"test-hash")
        db.add(teacher)
        db.commit()
        token = auth.issue_token(teacher.id)
    headers = {"Authorization": f"Bearer {token}"}
    c = e["client"]
    for path in [f"/exams/{e['exam']}/preflight", f"/interventions/{e['iv']}/teaching-card",
                 f"/classes/{e['class']}/retest-schedule",
                 f"/students/{e['students'][0]}/knowledge-points/{e['kp']}/evidence"]:
        assert c.get(path, headers=headers).status_code == 403
    assert c.post(f"/interventions/{e['iv']}/confirm", headers=headers).status_code == 403
    assert c.patch(f"/interventions/{e['iv']}/retest-schedule", headers=headers, json={"retest_due_date": date.today().isoformat()}).status_code == 403


def test_legacy_schedule_migration_is_idempotent(client, monkeypatch):
    from app import db as dbmod
    from scripts import migrate_retest_schedule
    c, factory = client
    with dbmod.engine.begin() as conn:
        conn.execute(text("ALTER TABLE intervention DROP COLUMN retest_due_date"))
        conn.execute(text("ALTER TABLE exam_response DROP COLUMN source_warnings_json"))
    monkeypatch.setattr(migrate_retest_schedule, "engine", dbmod.engine)
    migrate_retest_schedule.add_retest_due_date()
    migrate_retest_schedule.add_retest_due_date()
    assert "retest_due_date" in {c["name"] for c in inspect(dbmod.engine).get_columns("intervention")}
    assert "source_warnings_json" in {c["name"] for c in inspect(dbmod.engine).get_columns("exam_response")}
