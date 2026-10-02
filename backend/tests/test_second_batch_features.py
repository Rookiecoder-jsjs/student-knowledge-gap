"""第二批：题源隔离、无副作用预览、工作队列覆盖与学生任务事实边界。"""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select, func

from app.config import MIN_EVIDENCE_COUNT
from app import auth
from app.db import utcnow
from app.kb.graph import KpGraph
from app.models import (Attribution, EvidenceEvent, ExamTemplate, ExamResponse, Intervention,
    KbVersion, KnowledgePoint, QuestionKp, Report, ResponseAnswer, Student, StudyRecord,
    Teacher, TeacherClass, TeachingProgress, TemplateQuestion)
from app.next_tasks import next_tasks
from tests.test_api_queries import client, _bootstrap, _create_exam  # noqa: F401


@pytest.fixture()
def env(client):
    c, factory = client
    cid, students = _bootstrap(c)
    eid = _create_exam(c, cid)
    with factory() as db:
        kp = db.scalar(select(KnowledgePoint).where(KnowledgePoint.code == "M7A-105"))
        other = db.scalar(select(KnowledgePoint).where(KnowledgePoint.code == "M7A-111"))
        db.add(TeachingProgress(class_id=cid, kp_id=kp.id, taught_at=date(2025, 1, 1)))
        db.add(TeachingProgress(class_id=cid, kp_id=other.id, taught_at=date(2025, 1, 1)))
        for q in db.scalars(select(TemplateQuestion).where(TemplateQuestion.exam_template_id == eid)):
            q.kps[0].reviewed_at = utcnow(); q.kps[0].reviewed_by = "teacher"
        for idx, cog in [(3, "应用"), (4, "应用"), (5, "理解")]:
            q = TemplateQuestion(exam_template_id=eid, idx=idx, stem=f"独立题{idx}", q_type="解答", full_score=5, cog_level=cog)
            db.add(q); db.flush()
            db.add(QuestionKp(template_question_id=q.id, kp_id=kp.id, weight=1, reviewed_at=utcnow(), reviewed_by="teacher"))
        att = Attribution(student_id=students[0], kp_id=kp.id, root_kp_id=other.id, type="前置缺陷", status="active", confidence=.7)
        iv = Intervention(class_id=cid, student_id=students[0], kp_id=kp.id, exam_id=eid,
            kind="evidence_boost", scope="student", baseline_as_of=datetime(2025,11,1))
        db.add_all([att, iv]); db.commit()
        return dict(c=c, factory=factory, cid=cid, students=students, eid=eid, kp=kp.id, other=other.id, att=att.id, iv=iv.id)


def preview(e, **params):
    return e["c"].get(f"/students/{e['students'][0]}/diagnostic-blueprint", params={"kp_id":e["kp"], **params})


def test_minimal_evidence_preview_and_confirmed_copy(env):
    e=env; c=e["c"]
    gaps=c.get(f"/students/{e['students'][0]}/evidence-gaps").json()
    assert next(g for g in gaps["items"] if g["kp_id"] == e["kp"])["questions_needed"] == MIN_EVIDENCE_COUNT
    plan=preview(e).json()
    assert plan["ready"] and len(plan["slots"]) == MIN_EVIDENCE_COUNT
    assert [s["question"]["cog_level"] for s in plan["slots"]] == ["理解"] + ["应用"] * (MIN_EVIDENCE_COUNT - 1)
    with e["factory"]() as db:
        assert db.scalar(select(func.count(ExamTemplate.id))) == 1
        assert db.scalar(select(func.count(EvidenceEvent.id))) == 0
    request={"kp_id":e["kp"],"mode":"evidence", "preview_token":plan["preview_token"], "question_ids":[s["question"]["id"] for s in plan["slots"]],"exam_date":date.today().isoformat()}
    created=c.post(f"/students/{e['students'][0]}/diagnostic-exams",json=request)
    assert created.status_code == 200, created.text
    with e["factory"]() as db:
        exam=db.get(ExamTemplate,created.json()["exam_id"])
        assert exam.type == "诊断" and len(exam.questions) == MIN_EVIDENCE_COUNT
        assert all(q.kps[0].reviewed_at is None for q in exam.questions)
        assert not exam.responses
        assert db.scalar(select(func.count(EvidenceEvent.id))) == 0
    assert c.get(f"/exams/{created.json()['exam_id']}/preflight").json()["warning_count"] > 0


def test_seen_stem_unreviewed_mixed_or_other_class_question_is_not_selected(env):
    e=env
    with e["factory"]() as db:
        questions=list(db.scalars(select(TemplateQuestion).where(TemplateQuestion.exam_template_id == e["eid"]).order_by(TemplateQuestion.idx)))
        response=ExamResponse(exam_template_id=e["eid"], student_id=e["students"][0], total_score=0, status="待审核")
        db.add(response); db.flush()
        db.add(ResponseAnswer(exam_response_id=response.id,template_question_id=questions[0].id,score=0))
        questions[-1].stem=" q1 "  # 同题干换主键也排除。
        questions[2].kps[0].reviewed_at=None
        db.add(QuestionKp(template_question_id=questions[3].id,kp_id=e["other"],reviewed_at=utcnow(),weight=1))
        db.commit()
    plan=preview(e).json()
    assert not plan["ready"] and all(s["question"] is None for s in plan["slots"])
    response=e["c"].post(f"/students/{e['students'][0]}/diagnostic-exams",json={"kp_id":e["kp"],"mode":"evidence","preview_token":plan["preview_token"],"question_ids":[1],"exam_date":date.today().isoformat()})
    assert response.status_code == 400


def test_preview_change_and_date_are_revalidated_before_any_write(env):
    e=env; plan=preview(e).json(); ids=[s["question"]["id"] for s in plan["slots"]]
    body={"kp_id":e["kp"],"mode":"evidence","preview_token":plan["preview_token"],"question_ids":ids,"exam_date":(date.today()-timedelta(days=1)).isoformat()}
    url=f"/students/{e['students'][0]}/diagnostic-exams"
    assert e["c"].post(url,json=body).status_code == 400
    body["exam_date"]=date.today().isoformat()
    with e["factory"]() as db:
        db.get(TemplateQuestion,ids[0]).kps[0].reviewed_at=None; db.commit()
    assert e["c"].post(url,json=body).status_code == 400
    with e["factory"]() as db:
        assert db.scalar(select(func.count(ExamTemplate.id))) == 1


def test_attribution_group_checks_target_root_and_requires_active_owned_hypothesis(env):
    e=env; c=e["c"]
    # 根知识点原题是应用题，符合前置基础检查。
    plan=preview(e,mode="attribution",attribution_id=e["att"]).json()
    assert plan["ready"] and [s["kp_id"] for s in plan["slots"]] == [e["other"],e["kp"]]
    with e["factory"]() as db:
        att=db.get(Attribution,e["att"]); att.status="overridden"; db.commit()
    assert preview(e,mode="attribution",attribution_id=e["att"]).status_code == 400
    assert c.get(f"/students/{e['students'][1]}/diagnostic-blueprint",params={"kp_id":e["kp"],"mode":"attribution","attribution_id":e["att"]}).status_code == 400


def test_untaught_points_are_not_assigned(env):
    e=env
    with e["factory"]() as db:
        progress=db.scalar(select(TeachingProgress).where(TeachingProgress.class_id==e["cid"],TeachingProgress.kp_id==e["kp"]))
        db.delete(progress); db.commit()
    assert preview(e).status_code == 400
    assert all(g["kp_id"] != e["kp"] for g in e["c"].get(f"/students/{e['students'][0]}/evidence-gaps").json()["items"])


def test_today_queue_includes_old_exams_overdue_first_and_paginates(env):
    e=env
    for i in range(7):
        new_id=_create_exam(e["c"], e["cid"], name=f"额外卷{i}")
        with e["factory"]() as db:
            for q in db.scalars(select(TemplateQuestion).where(TemplateQuestion.exam_template_id==new_id)):
                q.kps[0].reviewed_at=None
            db.commit()
    with e["factory"]() as db:
        iv=db.get(Intervention,e["iv"]);iv.status="done";iv.done_at=datetime.now()-timedelta(days=9);iv.retest_due_date=date.today()-timedelta(days=1)
        db.add(Report(type="student_diagnosis",class_id=e["cid"],student_id=e["students"][0],status="draft",content_markdown="待审核"));db.commit()
    result=e["c"].get(f"/classes/{e['cid']}/today-tasks",params={"limit":2}).json()
    assert result["items"][0]["kind"] == "retest"
    assert result["counts"]["tags"] == 7 and result["counts"]["report"] == 1
    assert result["total"] == 9 and result["has_more"]
    tail=e["c"].get(f"/classes/{e['cid']}/today-tasks",params={"offset":8,"limit":2}).json()
    assert len(tail["items"]) == 1 and not tail["has_more"]
    assert e["c"].get(f"/classes/{e['cid']}/today-tasks",params={"offset":-1}).status_code == 422


def test_student_cards_are_read_only_personal_and_preview_matches(env):
    e=env
    with e["factory"]() as db:
        student=db.get(Student,e["students"][0]); graph=KpGraph(db,1)
        before=db.scalar(select(func.count(StudyRecord.id)))
        cards=next_tasks(db,graph,student)
        assert cards["items"][0]["kp_code"] == graph.kp(e["kp"]).code
        assert len(cards["items"]) <= 3 and cards["items"][0]["status"] == "study"
        assert db.scalar(select(func.count(StudyRecord.id))) == before
        assert db.scalar(select(func.count(EvidenceEvent.id))) == 0
        record=StudyRecord(class_id=e["cid"],student_id=student.id,kp_id=e["kp"],plan_markdown="已有方案",
            generated_at=datetime.now(),self_marked_at=datetime.now())
        db.add(record);db.flush()
        marked=next_tasks(db,graph,student)["items"][0]
        assert marked["status"] == "awaiting_retest" and marked["self_marked"]
        other=next_tasks(db,graph,db.get(Student,e["students"][1]))
        assert not other["items"]


def test_auth_class_and_self_boundaries(env):
    e=env
    with e["factory"]() as db:
        student=db.get(Student,e["students"][0]);auth.enable_student_login(db,student.id,"password",username="student")
        teacher=Teacher(school_id=student.school_id,name="无班权限教师",username="outside",salt=b"s"*16,password_hash=b"x",admin=False)
        admin=Teacher(school_id=student.school_id,name="管理员",username="admin",salt=b"a"*16,password_hash=b"x",admin=True)
        db.add_all([teacher,admin]);db.flush()
        student_token=auth.issue_student_token(student.id)
        outside_token=auth.issue_token(teacher.id);admin_token=auth.issue_token(admin.id);db.commit()
    c=e["c"]
    c.headers["Authorization"]=f"Bearer {outside_token}"
    assert c.get(f"/classes/{e['cid']}/today-tasks").status_code == 403
    assert preview(e).status_code == 403
    c.headers["Authorization"]=f"Bearer {student_token}"
    own=c.get("/me/next-tasks");assert own.status_code == 200
    assert own.json()["student_id"] == e["students"][0]
    assert c.get(f"/classes/{e['cid']}/today-tasks").status_code == 403
    assert preview(e).status_code == 403
    c.headers["Authorization"]=f"Bearer {admin_token}"
    mirrored=c.get(f"/admin/students/{e['students'][0]}/portal/next-tasks")
    assert mirrored.json() == own.json()
    with e["factory"]() as db:
        assert db.scalar(select(func.count(StudyRecord.id))) == 0


def test_subject_and_version_filter_excludes_legacy_other_subject_sources(env):
    from app.queries.teacher_workbench import teacher_workbench
    from app.diagnostic_tasks import diagnostic_blueprint
    e=env
    with e["factory"]() as db:
        # 科任学科覆盖班级默认，NULL 来源仍属于班级默认学科。
        db.get(KbVersion,1).subject="英语"
        db.get(ExamTemplate,e["eid"]).subject=None
        db.add(Report(type="student_diagnosis",class_id=e["cid"],student_id=e["students"][0],status="draft",content_markdown="数学草稿"))
        db.commit()
        result=teacher_workbench(db,e["cid"],"英语",{e["kp"]},date.today())
        assert result["total"] == 0
        plan=diagnostic_blueprint(db,KpGraph(db,1),db.get(Student,e["students"][0]),e["kp"],"evidence",None,datetime.now())
    assert not plan["ready"] and all(s["question"] is None for s in plan["slots"])
    assert preview(e).status_code == 400  # 班级默认数学的知识库已不存在。


def test_student_cards_cap_and_new_round_does_not_reuse_old_self_report(env):
    e=env
    with e["factory"]() as db:
        ids=list(db.scalars(select(KnowledgePoint.id).where(KnowledgePoint.kb_version_id==1,KnowledgePoint.grade==7,KnowledgePoint.code.notlike("C%"))))[:5]
        for kp_id in ids:
            if kp_id not in (e["kp"],e["other"]):
                db.add(TeachingProgress(class_id=e["cid"],kp_id=kp_id,taught_at=date(2025,1,1)))
            db.add(Intervention(class_id=e["cid"],student_id=e["students"][0],kp_id=kp_id,exam_id=e["eid"],kind="reteach",scope="student",baseline_as_of=datetime(2025,11,1),suggested_at=datetime.now()))
        db.add(StudyRecord(class_id=e["cid"],student_id=e["students"][0],kp_id=e["kp"],plan_markdown="上一轮",generated_at=datetime.now()-timedelta(days=2),self_marked_at=datetime.now()-timedelta(days=1)))
        db.flush()
        result=next_tasks(db,KpGraph(db,1),db.get(Student,e["students"][0]))
        assert len(result["items"]) == 3 and result["remaining"] >= 2
        assert all(not item["self_marked"] for item in result["items"])


@pytest.mark.parametrize("kind", ["遗忘衰减", "易混淆", "前置缺陷"])
def test_hypothesis_slots_are_bounded_and_preserve_inference_limits(env, kind):
    e=env
    with e["factory"]() as db:
        att=db.get(Attribution,e["att"]);att.type=kind;db.commit()
    plan=preview(e,mode="attribution",attribution_id=e["att"]).json()
    assert 2 <= len(plan["slots"]) <= 3
    if kind == "遗忘衰减":
        assert "间隔复测" in plan["guidance"]
    if kind == "前置缺陷":
        assert plan["slots"][0]["kp_id"] == e["other"]


def test_other_class_question_cannot_fill_missing_source(env):
    from app.models import Class
    e=env
    with e["factory"]() as db:
        original=db.get(ExamTemplate,e["eid"])
        other=Class(school_id=original.clazz.school_id,name="其他班",grade=7,subject="数学")
        db.add(other);db.flush();original.class_id=other.id;db.commit()
    plan=preview(e).json()
    assert not plan["ready"] and all(s["question"] is None for s in plan["slots"])


def test_same_question_id_changed_content_requires_new_preview(env):
    e=env; plan=preview(e).json()
    ids=[s["question"]["id"] for s in plan["slots"]]
    with e["factory"]() as db:
        db.get(TemplateQuestion,ids[0]).stem="修改后的题面";db.commit()
    response=e["c"].post(f"/students/{e['students'][0]}/diagnostic-exams",json={
        "kp_id":e["kp"],"mode":"evidence","question_ids":ids,"preview_token":plan["preview_token"],"exam_date":date.today().isoformat()})
    assert response.status_code == 400
    fresh=preview(e).json()
    assert [s["question"]["id"] for s in fresh["slots"]] == ids
    assert fresh["preview_token"] != plan["preview_token"]
    with e["factory"]() as db:
        assert db.scalar(select(func.count(ExamTemplate.id))) == 1


def test_confusable_group_checks_the_partner_named_in_the_hypothesis(env):
    e=env
    with e["factory"]() as db:
        graph=KpGraph(db,1)
        partners=sorted(graph.confusable_partners(e["kp"]))
        assert len(partners) >= 2
        named=partners[-1]
        att=db.get(Attribution,e["att"]);att.type="易混淆";att.evidence_json=[{"confused_with":graph.kp(named).code}];db.commit()
    plan=preview(e,mode="attribution",attribution_id=e["att"]).json()
    assert plan["slots"][-1]["kp_id"] == named
