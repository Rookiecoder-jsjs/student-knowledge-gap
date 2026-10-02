"""Disposable E2E server: real API/DB/auth, deterministic model, loopback only."""
from __future__ import annotations
import os
import secrets
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_temp = tempfile.TemporaryDirectory(prefix="sc-e2e-")
# Never load the developer's .env or call their model/gateway.
for key in list(os.environ):
    if key.startswith("SC_"):
        del os.environ[key]
os.environ.update(PYTHON_DOTENV_DISABLED="1", SC_DATABASE_URL=f"sqlite:///{_temp.name}/e2e.db",
                  SC_AUTH_SECRET=secrets.token_hex(32), SC_JOB_QUEUE_ENABLE="1",
                  SC_OBJECT_STORAGE_DIR=f"{_temp.name}/objects", SC_LLM_PROVIDER="mock")

from app.db import Base, engine, SessionLocal, utcnow
from app.models import School, Class, Student, Teacher, TeacherClass, Report, ExamTemplate, TemplateQuestion, QuestionKp, KnowledgePoint, TeachingProgress, ExamResponse, ResponseAnswer, Intervention, EvidenceEvent
from app.auth import hash_password, enable_student_login
from app.kb.loader import import_kb
from app.llm.client import BaseClient, set_client
from sqlalchemy import select

Base.metadata.create_all(engine)
with SessionLocal() as db:
    kb = import_kb(db, ROOT / "kb/math/grade7/kb.yaml")
    kb.status = "active"
    school = School(name="回归测试学校"); db.add(school); db.flush()
    clazz = Class(school_id=school.id, name="七年级回归班", grade=7, subject="数学")
    db.add(clazz); db.flush()
    for username, admin in [("teacher", False), ("admin", True)]:
        salt = secrets.token_bytes(16)
        teacher = Teacher(school_id=school.id, name="测试教师" if not admin else "测试管理员", username=username,
                          salt=salt, password_hash=hash_password("test-password", salt), admin=admin)
        db.add(teacher); db.flush()
        db.add(TeacherClass(teacher_id=teacher.id, class_id=clazz.id, subject="数学"))
    student = Student(school_id=school.id, class_id=clazz.id, name_or_alias="测试学生", external_code="S001")
    db.add(student); db.flush()
    enable_student_login(db, student.id, "test-password", username="student")
    exam = ExamTemplate(class_id=clazz.id, name="回归样例", exam_date=date(2026, 9, 1), type="单元", subject="数学")
    db.add(exam); db.flush()
    for index, code in enumerate(["M7A-105", "M7A-111"], 1):
        kp = db.scalar(select(KnowledgePoint).where(KnowledgePoint.kb_version_id == kb.id, KnowledgePoint.code == code))
        question = TemplateQuestion(exam_template_id=exam.id, idx=index, stem=f"样例题{index}", q_type="解答", full_score=10, cog_level="应用")
        db.add(question); db.flush()
        db.add(QuestionKp(template_question_id=question.id, kp_id=kp.id, weight=1, reviewed_by="teacher", reviewed_at=utcnow()))
        db.add(TeachingProgress(class_id=clazz.id, kp_id=kp.id, taught_at=date(2026, 9, 1)))
    db.add(Report(type="student_diagnosis", class_id=clazz.id, student_id=student.id, status="issued", content_markdown="# 已签发的诊断\n\n先练习，再通过复测验证。"))
    db.add(Report(type="student_diagnosis", class_id=clazz.id, student_id=student.id, status="draft", content_markdown="# 待签发回归报告\n\n复核后签发给学生。"))
    # 独立的遗留脏数据样例，用于验证预检补录到诊断/复测安排的完整操作。
    check_exam = ExamTemplate(class_id=clazz.id, name="质量预检样例", exam_date=date(2026, 9, 2), type="单元", subject="数学")
    db.add(check_exam); db.flush()
    kp = db.scalar(select(KnowledgePoint).where(KnowledgePoint.kb_version_id == kb.id, KnowledgePoint.code == "M7A-105"))
    questions = []
    for idx in (1, 2):
        q = TemplateQuestion(exam_template_id=check_exam.id, idx=idx, stem=f"绝对值检查题{idx}", q_type="解答", full_score=10, cog_level="应用")
        db.add(q); db.flush(); questions.append(q)
        db.add(QuestionKp(template_question_id=q.id, kp_id=kp.id, weight=1, reviewed_by="teacher", reviewed_at=utcnow()))
    response = ExamResponse(exam_template_id=check_exam.id, student_id=student.id, total_score=99, status="待审核")
    db.add(response); db.flush()
    db.add(ResponseAnswer(exam_response_id=response.id, template_question_id=questions[0].id, score=99))
    db.add(Intervention(class_id=clazz.id, student_id=None, kp_id=kp.id, exam_id=exam.id,
                        kind="reteach", scope="class", baseline_as_of=datetime(2026, 9, 2)))
    # 第二批使用独立班级，避免新题源或学习记录改变第一批的验证口径。
    task_class = Class(school_id=school.id, name="任务卡回归班", grade=7, subject="数学")
    db.add(task_class); db.flush()
    teacher = db.scalar(select(Teacher).where(Teacher.username == "teacher"))
    db.add(TeacherClass(teacher_id=teacher.id, class_id=task_class.id, subject="数学"))
    task_student = Student(school_id=school.id, class_id=task_class.id, name_or_alias="任务学生", external_code="S002")
    db.add(task_student); db.flush()
    enable_student_login(db, task_student.id, "test-password", username="student-next")
    source = ExamTemplate(class_id=task_class.id, name="已审核独立题源", exam_date=date(2026,9,15), type="练习", subject="数学")
    history = ExamTemplate(class_id=task_class.id, name="归因作答样例", exam_date=date(2026,9,16), type="单元", subject="数学")
    db.add_all([source, history]); db.flush()
    idx = 0
    for code in ["M7A-105", "M7A-111", "M7A-106"]:
        point = db.scalar(select(KnowledgePoint).where(KnowledgePoint.kb_version_id == kb.id, KnowledgePoint.code == code))
        db.add(TeachingProgress(class_id=task_class.id, kp_id=point.id, taught_at=date(2026,9,1)))
        for cog in ["理解", "应用"]:
            idx += 1
            q = TemplateQuestion(exam_template_id=source.id, idx=idx, stem=f"{point.name}·{cog}独立检查题", q_type="解答", full_score=10, cog_level=cog)
            db.add(q); db.flush()
            db.add(QuestionKp(template_question_id=q.id, kp_id=point.id, weight=1, reviewed_by="teacher", reviewed_at=utcnow()))
    response = ExamResponse(exam_template_id=history.id, student_id=task_student.id, total_score=6, status="已提交")
    db.add(response); db.flush()
    idx = 0
    for code in ["M7A-111", "M7A-106"]:
        point = db.scalar(select(KnowledgePoint).where(KnowledgePoint.kb_version_id == kb.id, KnowledgePoint.code == code))
        for _ in range(3):
            idx += 1
            q = TemplateQuestion(exam_template_id=history.id, idx=idx, stem=f"已作答{idx}", q_type="解答", full_score=10, cog_level="应用")
            db.add(q); db.flush()
            db.add(QuestionKp(template_question_id=q.id, kp_id=point.id, weight=1, reviewed_by="teacher", reviewed_at=utcnow()))
            answer = ResponseAnswer(exam_response_id=response.id, template_question_id=q.id, score=1)
            db.add(answer); db.flush()
            db.add(EvidenceEvent(student_id=task_student.id, kp_id=point.id, response_answer_id=answer.id,
                source_type="单元", value=.1, weight=1, cog_level="应用", occurred_at=datetime(2026,9,16,12), algo_version="e2e"))
    db.commit()


class FixtureModel(BaseClient):
    model_version = "e2e-fixture"
    def parse_json(self, system, user, image_bytes):
        if image_bytes:
            time.sleep(1)
            return {"questions": [{"idx": 1, "stem": "绝对值", "q_type": "解答", "full_score": 10,
                    "cog_level": "应用", "kp_tags": [{"code": "M7A-105", "weight": 1}], "confidence": 0.95}],
                    "answers": [{"idx": 1, "score": 6, "confidence": 0.95}, {"idx": 2, "score": 8, "confidence": 0.95}]}
        return {"markdown": "请结合已有作答证据安排练习，并在后续考试中验证。"}


set_client(FixtureModel())
from app.main import app

if __name__ == "__main__":
    import uvicorn
    try:
        uvicorn.run(app, host="127.0.0.1", port=18000)
    finally:
        engine.dispose()
        _temp.cleanup()
