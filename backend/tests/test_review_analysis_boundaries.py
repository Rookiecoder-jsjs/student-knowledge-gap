"""Regression cases for analysis scope, intervention authorization, and entity IDs."""

from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import auth
from app.api.routers import intervention as routes
from app.intervention import action_plan_view, generate_interventions, intervention_summary
from app.kb.graph import KpGraph
from app.models import (
    Attribution, Class, ExamResponse, ExamTemplate, Intervention, KbVersion,
    KnowledgePoint, Report, ResponseAnswer, Teacher, TeacherClass, TemplateQuestion,
)
from app.pipeline.attribution import materialize_attribution_verdicts
from app.reports.quality_model import compute_quality_model
from app.api.routers import ingestion as ingestion_routes, org as org_routes
from app.schemas import ManualScores


@pytest.mark.parametrize("operation", ["detail", "responses", "preflight", "approve", "manual", "commit"])
def test_exam_routes_reject_same_class_other_subject(session, scope_env, operation):
    e = scope_env
    exam_id = e["exams"]["math"].id
    calls = {
        "detail": lambda: org_routes.exam_detail(exam_id, ctx=e["ctx"], db=session),
        "responses": lambda: org_routes.exam_responses(exam_id, ctx=e["ctx"], db=session),
        "preflight": lambda: ingestion_routes.preflight(exam_id, ctx=e["ctx"], db=session),
        "approve": lambda: ingestion_routes.approve_tags(exam_id, ctx=e["ctx"], db=session),
        "manual": lambda: ingestion_routes.manual_entry(exam_id, ManualScores(student_id=next(iter(e["students"].values())), scores={}), ctx=e["ctx"], db=session),
        "commit": lambda: ingestion_routes.commit(exam_id, ctx=e["ctx"], db=session),
    }
    with pytest.raises(HTTPException) as exc:
        calls[operation]()
    assert exc.value.status_code == 403


@pytest.mark.parametrize("paged", [False, True])
def test_exam_lists_filter_subject_before_pagination(session, scope_env, paged):
    e = scope_env
    result = org_routes.list_exams(class_id=e["class"].id, offset=0 if paged else None,
                                  limit=1 if paged else None, ctx=e["ctx"], db=session)
    assert [row["exam_id"] for row in result["exams"]] == [e["exams"]["english"].id]
    if paged:
        assert result["total"] == 1 and not result["has_more"]


@pytest.fixture()
def scope_env(session, env):
    clazz = env["class"]
    env["kb"].status = "active"
    env["kb"].grade = 7
    english = KbVersion(subject="英语", grade=7, textbook_edition="测试版", status="active")
    history = KbVersion(subject="数学", grade=7, textbook_edition="旧版", status="reviewed")
    session.add_all([english, history])
    session.flush()
    english_kp = KnowledgePoint(kb_version_id=english.id, code="E1", name="英语点", grade=7)
    history_kp = KnowledgePoint(kb_version_id=history.id, code="P1", name="历史点", grade=7)
    teacher = Teacher(school_id=clazz.school_id, name="英语教师", username="english", admin=False)
    session.add_all([english_kp, history_kp, teacher])
    session.flush()
    session.add(TeacherClass(teacher_id=teacher.id, class_id=clazz.id, subject="英语"))
    exams = {}
    for key, subject in (("math", "数学"), ("english", "英语"), ("legacy", None)):
        exam = ExamTemplate(class_id=clazz.id, subject=subject, name=key,
                            exam_date=date(2026, 1, 2), type="单元")
        session.add(exam)
        exams[key] = exam
    session.flush()
    yield {**env, "english_kp": english_kp.id, "history_kp": history_kp.id,
           "exams": exams, "ctx": auth.AccessContext(teacher=teacher)}
    auth.reset_mode_cache_for_tests()


def _iv(session, env, subject="english", *, kp_id=None, status="suggested", scope="student"):
    row = Intervention(
        class_id=env["class"].id,
        student_id=None if scope == "class" else next(iter(env["students"].values())),
        kp_id=kp_id or (env["english_kp"] if subject == "english" else env["kp"]["P1"]),
        exam_id=env["exams"][subject].id, kind="evidence_boost", scope=scope,
        group_ref="shared-reference" if scope == "group" else None,
        baseline_as_of=datetime(2026, 1, 2, 23, 59), status=status,
        done_at=datetime(2026, 1, 3) if status == "done" else None,
        retest_due_date=date.today() + timedelta(days=7) if status == "done" else None,
        note="keep original",
    )
    session.add(row)
    session.flush()
    return row


def test_analysis_preserves_other_subject_and_historical_version(session, scope_env):
    e = scope_env
    sid = next(iter(e["students"].values()))
    foreign = [
        _iv(session, e), _iv(session, e),
        _iv(session, e, "math", kp_id=e["history_kp"]),
    ]
    attrs = [Attribution(student_id=sid, kp_id=kp_id, type="遗忘衰减", status="active")
             for kp_id in (e["english_kp"], e["history_kp"], e["kp"]["P1"])]
    session.add_all(attrs)
    session.add(ExamResponse(exam_template_id=e["exams"]["math"].id,
                             student_id=sid, status="已提交"))
    session.flush()
    graph = KpGraph(session, e["kb"].id)
    materialize_attribution_verdicts(session, graph, sid, e["class"].id, datetime(2026, 1, 3))
    generate_interventions(session, graph, e["class"].id,
                           e["exams"]["math"].id, datetime(2026, 1, 3))
    assert [a.status for a in attrs] == ["active", "active", "resolved"]
    assert [(r.status, r.note) for r in foreign] == [("suggested", "keep original")] * 3
    assert all(session.get(Intervention, r.id) is not None for r in foreign)


def test_graph_scope_applies_to_action_queue_and_metrics(session, scope_env):
    e = scope_env
    _iv(session, e, "math", scope="class")
    _iv(session, e, scope="class")
    _iv(session, e, "math", kp_id=e["history_kp"], scope="class")
    graph = KpGraph(session, e["kb"].id)
    view = action_plan_view(session, graph, e["class"].id)
    assert view["pending_confirm"] == 1
    assert [r["kp_code"] for r in view["rows"]] == ["P1"]
    assert intervention_summary(session, graph, e["class"].id)["total"] == 1


@pytest.mark.parametrize("endpoint", [
    routes.confirm_intervention, routes.skip_intervention, routes.get_teaching_card,
    routes.single_effect, routes.reschedule_retest,
])
def test_other_subject_resource_operations_are_forbidden(session, scope_env, endpoint):
    row = _iv(session, scope_env, "math")
    kwargs = {"intervention_id": row.id, "ctx": scope_env["ctx"], "db": session}
    if endpoint in (routes.confirm_intervention, routes.skip_intervention):
        kwargs.update(req=None, with_group=False)
    elif endpoint is routes.reschedule_retest:
        kwargs.update(req=routes.RetestScheduleRequest(retest_due_date=date.today()), with_group=False)
    with pytest.raises(HTTPException) as exc:
        endpoint(**kwargs)
    assert exc.value.status_code == 403
    assert row.status == "suggested"


def test_lists_and_summaries_only_expose_authorized_sources(session, scope_env):
    e = scope_env
    english = _iv(session, e)
    _iv(session, e, "math")
    _iv(session, e, "legacy")
    rows = routes.list_interventions(class_id=e["class"].id, student_id=None,
                                    status=None, offset=0, limit=50, ctx=e["ctx"], db=session)
    assert rows["total"] == 1 and [r["id"] for r in rows["items"]] == [english.id]
    summary = routes.intervention_student_summary(e["class"].id, ctx=e["ctx"], db=session)
    assert summary["students"][0]["suggested"] == 1
    assert routes.interventions_summary(e["class"].id, ctx=e["ctx"], db=session)["total"] == 1
    assert routes.class_action_plan(e["class"].id, exam_id=None,
                                   ctx=e["ctx"], db=session)["pending_confirm"] == 1


@pytest.mark.parametrize("operation", ["confirm", "skip", "reschedule"])
def test_group_actions_cannot_mutate_other_subject_members(session, scope_env, operation):
    e = scope_env
    status = "done" if operation == "reschedule" else "suggested"
    owned = _iv(session, e, status=status, scope="group")
    denied = _iv(session, e, "math", status=status, scope="group")
    if operation == "reschedule":
        original_due = denied.retest_due_date
        due = date.today() + timedelta(days=9)
        result = routes.reschedule_retest(owned.id, routes.RetestScheduleRequest(retest_due_date=due),
                                          with_group=True, ctx=e["ctx"], db=session)
        assert result["updated"] == 1
        assert owned.retest_due_date == due and denied.retest_due_date == original_due
    else:
        endpoint = routes.confirm_intervention if operation == "confirm" else routes.skip_intervention
        result = endpoint(owned.id, req=None, with_group=True, ctx=e["ctx"], db=session)
        assert result["confirmed" if operation == "confirm" else "skipped"] == 1
        assert owned.status == ("done" if operation == "confirm" else "skipped")
        assert denied.status == "suggested"


@pytest.mark.parametrize("role", ["admin", "homeroom", "all_subjects", "math"])
def test_batch_authorization_preserves_existing_exemptions(session, scope_env, role):
    e = scope_env
    english = _iv(session, e)
    math = _iv(session, e, "math")
    legacy = _iv(session, e, "legacy")
    if role == "admin":
        e["ctx"].teacher.admin = True
    elif role == "homeroom":
        e["class"].homeroom_teacher_id = e["ctx"].teacher.id
    else:
        grant = session.scalar(select(TeacherClass).where(TeacherClass.teacher_id == e["ctx"].teacher.id))
        grant.subject = None if role == "all_subjects" else "数学"
    session.flush()
    rows = routes.list_interventions(class_id=e["class"].id, student_id=None,
                                    status=None, offset=0, limit=50, ctx=e["ctx"], db=session)
    expected = {math.id, legacy.id} if role == "math" else {english.id, math.id, legacy.id}
    assert {r["id"] for r in rows["items"]} == expected
    assert all(r["loop_state"] == "未学到" for r in rows["items"])


def test_source_exam_class_must_match_intervention_class(session, scope_env):
    e = scope_env
    other = Class(school_id=e["class"].school_id, name="其他班", grade=7, subject="英语")
    session.add(other)
    session.flush()
    source = ExamTemplate(class_id=other.id, subject="英语", name="other",
                          exam_date=date.today(), type="练习")
    session.add(source)
    session.flush()
    row = _iv(session, e)
    row.exam_id = source.id
    session.flush()
    with pytest.raises(HTTPException) as exc:
        routes.skip_intervention(row.id, req=None, with_group=False, ctx=e["ctx"], db=session)
    assert exc.value.status_code == 404
    listed = routes.list_interventions(class_id=e["class"].id, student_id=None,
                                      status=None, offset=0, limit=50, ctx=e["ctx"], db=session)
    assert listed["total"] == 0


def test_student_action_plan_does_not_return_other_subject_cache(session, scope_env):
    e = scope_env
    sid = next(iter(e["students"].values()))
    reports = []
    for subject in ("english", "math"):
        report = Report(type="student_action_plan", class_id=e["class"].id, student_id=sid,
                        exam_id=e["exams"][subject].id, content_markdown=subject)
        session.add(report)
        reports.append(report)
    session.flush()
    result = routes.student_action_plan(sid, exam_id=None, as_of=None, ctx=e["ctx"], db=session)
    assert result["report_id"] == reports[0].id and result["markdown"] == "english"
    with pytest.raises(HTTPException) as exc:
        routes.student_action_plan(sid, exam_id=e["exams"]["math"].id,
                                   as_of=None, ctx=e["ctx"], db=session)
    assert exc.value.status_code == 403


def test_non_exam_action_plan_cache_retains_its_subject(session, scope_env, monkeypatch):
    from app.reports import student_action_plan as plans

    # The language model is optional; this regression covers the stored scope.
    monkeypatch.setattr(plans, "write_student_action_plan", lambda *args: None)
    e = scope_env
    sid = next(iter(e["students"].values()))
    session.add(Report(type="student_action_plan", class_id=e["class"].id,
                       student_id=sid, content_markdown="旧报告，学科不明"))
    session.flush()
    first = routes.student_action_plan(sid, exam_id=None, as_of=None, ctx=e["ctx"], db=session)
    session.flush()
    second = routes.student_action_plan(sid, exam_id=None, as_of=None, ctx=e["ctx"], db=session)
    assert first["report_id"] == second["report_id"]
    stored = session.get(Report, first["report_id"])
    assert stored.snapshot_json["kb_version_id"] == session.get(KnowledgePoint, e["english_kp"]).kb_version_id
    assert first["markdown"] != "旧报告，学科不明"


def test_question_rates_use_response_ids_across_multiple_exams(session, env):
    sid = next(iter(env["students"].values()))
    graph = KpGraph(session, env["kb"].id)
    for index, score in enumerate((10.0, 2.0), 1):
        exam = ExamTemplate(class_id=env["class"].id, subject="数学", name=f"exam{index}",
                            exam_date=date(2026, 1, index), type="单元")
        session.add(exam)
        session.flush()
        response = ExamResponse(id=100 + index, exam_template_id=exam.id,
                                student_id=sid, status="已提交", total_score=score)
        question = TemplateQuestion(exam_template_id=exam.id, idx=1, stem="q",
                                    q_type="解答", full_score=10.0)
        session.add_all([response, question])
        session.flush()
        session.add(ResponseAnswer(exam_response_id=response.id, template_question_id=question.id,
                                   score=score))
        session.flush()
        model = compute_quality_model(session, graph, env["class"].id, exam.id)
        assert model.question_rates[0]["rate"] == pytest.approx(score / 10)
        assert model.question_rates[0]["low"] == (score < 6)
