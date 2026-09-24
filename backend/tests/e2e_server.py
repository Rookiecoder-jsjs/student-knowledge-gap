"""Disposable E2E server: real API/DB/auth, deterministic model, loopback only."""
from __future__ import annotations
import os
import secrets
import sys
import tempfile
import time
from datetime import date
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
from app.models import School, Class, Student, Teacher, TeacherClass, Report, ExamTemplate, TemplateQuestion, QuestionKp, KnowledgePoint, TeachingProgress
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
