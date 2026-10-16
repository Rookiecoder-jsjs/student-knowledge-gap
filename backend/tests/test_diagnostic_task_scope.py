"""个体诊断在建卷、采集、预检和报告之间保持同一个目标与来源。"""

from datetime import date

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.db import utcnow
from app.ingestion.commit import add_manual_response, commit_exam
from app.ingestion.excel import import_excel
from app.ingestion.photo import PhotoParseResult, _persist_response_from_payload
from app.ingestion.preflight import exam_preflight
from app.kb.graph import KpGraph
from app.models import DiagnosticTask, ExamResponse, ExamTemplate, Report, Student, TemplateQuestion
from app.reports.auto_generate import generate_exam_reports
from app.reports.quality_model import compute_quality_model
from tests.test_api_queries import client  # noqa: F401
from tests.test_second_batch_features import env, preview  # noqa: F401


def create_task(e, mode="evidence"):
    params = {"mode": mode}
    if mode == "attribution":
        params["attribution_id"] = e["att"]
    plan = preview(e, **params).json()
    assert plan["ready"]
    result = e["c"].post(f"/students/{e['students'][0]}/diagnostic-exams", json={
        "kp_id": e["kp"], "mode": mode,
        "attribution_id": params.get("attribution_id"),
        "question_ids": [slot["question"]["id"] for slot in plan["slots"]],
        "preview_token": plan["preview_token"], "exam_date": date.today().isoformat(),
    })
    assert result.status_code == 200, result.text
    return result.json()["exam_id"], plan


@pytest.mark.parametrize("mode", ["evidence", "attribution"])
def test_task_identity_and_single_student_roster_survive_readback(env, mode):
    e = env
    exam_id, plan = create_task(e, mode)
    with e["factory"]() as db:
        task = db.scalar(select(DiagnosticTask).where(DiagnosticTask.exam_id == exam_id))
        assert (task.student_id, task.kp_id, task.mode) == (e["students"][0], e["kp"], mode)
        assert task.attribution_id == (e["att"] if mode == "attribution" else None)
        assert task.source_question_ids_json == [slot["question"]["id"] for slot in plan["slots"]]
        assert task.preview_token == plan["preview_token"]
    detail = e["c"].get(f"/exams/{exam_id}").json()
    assert detail["diagnostic_task"]["student_id"] == e["students"][0]
    matrix = e["c"].get(f"/exams/{exam_id}/responses").json()
    assert [row["student_id"] for row in matrix["responses"]] == [e["students"][0]]
    assert matrix["summary"]["未采集"] == 1


def test_wrong_student_is_rejected_by_manual_photo_and_excel(env, tmp_path):
    e = env
    exam_id, _ = create_task(e)
    with e["factory"]() as db:
        exam = db.get(ExamTemplate, exam_id)
        scores = {q.idx: q.full_score for q in exam.questions}
        with pytest.raises(ValueError, match="指定的学生"):
            add_manual_response(db, exam_id, e["students"][1], scores)
        with pytest.raises(ValueError, match="指定的学生"):
            _persist_response_from_payload(db, exam, e["students"][1], {}, PhotoParseResult())
        other = db.get(Student, e["students"][1])
        book = Workbook()
        sheet = book.active
        sheet.append(["姓名", *[f"Q{i}" for i in scores]])
        sheet.append([other.name_or_alias, *scores.values()])
        path = tmp_path / "other.xlsx"
        book.save(path)
        imported = import_excel(db, exam_id, path)
        assert imported.imported == 0 and imported.unmatched_students
        assert not list(db.scalars(select(ExamResponse).where(ExamResponse.exam_template_id == exam_id)))


def test_target_submission_does_not_report_rest_of_class_as_missing(env):
    e = env
    exam_id, _ = create_task(e)
    with e["factory"]() as db:
        exam = db.get(ExamTemplate, exam_id)
        for q in exam.questions:
            for tag in q.kps:
                tag.reviewed_by, tag.reviewed_at = "teacher", utcnow()
        scores = {q.idx: q.full_score for q in exam.questions}
        add_manual_response(db, exam_id, e["students"][0], scores)
        check = exam_preflight(db, exam_id)
        assert check["ready"]
        assert not any(issue["code"] == "uncollected" for issue in check["issues"])
        assert commit_exam(db, exam_id).committed_responses == 1
        model = compute_quality_model(db, KpGraph(db, 1), e["cid"], exam_id)
        assert model.pending == 0 and model.committed == 1
        result = generate_exam_reports(db, exam_id)
        assert result.diagnoses == 1 and not result.quality
        reports = list(db.scalars(select(Report).where(Report.exam_id == exam_id)))
        assert {report.type for report in reports} == {"student_diagnosis", "student_action_plan"}
        assert all(report.student_id == e["students"][0] for report in reports)


def test_programmatic_commit_cannot_bypass_target_guard(env):
    e = env
    exam_id, _ = create_task(e)
    with e["factory"]() as db:
        db.add(ExamResponse(exam_template_id=exam_id, student_id=e["students"][1], status="待审核", source="manual"))
        db.flush()
        assert not exam_preflight(db, exam_id)["ready"]
        with pytest.raises(ValueError, match="指定的学生"):
            commit_exam(db, exam_id)


def test_legacy_task_without_identity_requires_explicit_recreation(env):
    e = env
    with e["factory"]() as db:
        legacy = ExamTemplate(class_id=e["cid"], subject="数学", source="diagnostic_task",
                              type="诊断", name="旧任务", exam_date=date.today())
        db.add(legacy)
        db.flush()
        check = exam_preflight(db, legacy.id)
        assert any(issue["code"] == "missing_task_target" for issue in check["issues"])
        with pytest.raises(ValueError, match="重新预览"):
            add_manual_response(db, legacy.id, e["students"][0], {})
        # Legacy answers cannot turn an unknown target into a negative pending count.
        db.add(ExamResponse(exam_template_id=legacy.id, student_id=e["students"][0], status="已提交", source="manual"))
        db.flush()
        model = compute_quality_model(db, KpGraph(db, 1), e["cid"], legacy.id)
        assert model.committed == 0 and model.pending == 0


def test_diagnostic_task_migration_upgrade_and_downgrade(tmp_path):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    url = f"sqlite:///{tmp_path / 'diagnostic-migration.db'}"
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "c2a6f8d4e0b1")
    engine = create_engine(url)
    assert not inspect(engine).has_table("diagnostic_task")
    engine.dispose()
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    assert inspect(engine).has_table("diagnostic_task")
    assert {column["name"] for column in inspect(engine).get_columns("diagnostic_task")} >= {
        "exam_id", "student_id", "kp_id", "mode", "attribution_id", "preview_token",
    }
    engine.dispose()
    command.downgrade(cfg, "c2a6f8d4e0b1")
    engine = create_engine(url)
    assert not inspect(engine).has_table("diagnostic_task")
    assert inspect(engine).has_table("exam_template")
    engine.dispose()


def test_individual_task_cannot_generate_class_report_on_demand(env):
    from app.reports.diagnosis_orchestrator import get_or_generate_quality_report

    e = env
    exam_id, _ = create_task(e)
    with e["factory"]() as db:
        with pytest.raises(ValueError, match="个体诊断"):
            get_or_generate_quality_report(db, KpGraph(db, 1), e["cid"], exam_id)
        assert not list(db.scalars(select(Report).where(Report.exam_id == exam_id)))
