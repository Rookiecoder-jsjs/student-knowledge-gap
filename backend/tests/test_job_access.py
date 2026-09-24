"""HTTP tests using real scope policy, isolated data and explicit principals."""
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth, jobs
from app.api.deps import get_db, require_teacher
from app.api.routers.jobs import router
from app.db import Base
from app.models import Class, ExamTemplate, KbVersion, School, Teacher, TeacherClass


@pytest.fixture()
def scoped():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        school = School(name="Test")
        db.add(school); db.flush()
        a, b = [Class(school_id=school.id, name=name, grade=7, subject="数学") for name in ["A", "B"]]
        teacher = Teacher(school_id=school.id, name="A teacher", username="a", salt=b"salt", password_hash=b"hash")
        db.add_all([a, b, teacher]); db.flush()
        db.add(TeacherClass(teacher_id=teacher.id, class_id=a.id, subject="数学"))
        exam_a = ExamTemplate(class_id=a.id, subject="数学", name="A exam", exam_date=date.today(), type="单元")
        exam_b = ExamTemplate(class_id=b.id, subject="数学", name="B exam", exam_date=date.today(), type="单元")
        wrong_subject = ExamTemplate(class_id=a.id, subject="语文", name="Other subject", exam_date=date.today(), type="单元")
        db.add_all([exam_a, exam_b, wrong_subject]); db.flush()
        kb = KbVersion(subject="数学", textbook_edition="test")
        other_kb = KbVersion(subject="语文", textbook_edition="test")
        db.add_all([kb, other_kb]); db.flush()
        specs = [("photo_template", {"class_id": a.id, "kb_id": kb.id}), ("photo_template", {"class_id": b.id, "kb_id": kb.id}),
                 ("photo_response", {"exam_id": exam_a.id}), ("exam_reports", {"exam_id": exam_b.id}),
                 ("photo_response", {"exam_id": wrong_subject.id}), ("unknown", {}),
                 ("photo_template", {"class_id": 999999}),
                 ("photo_template", {"class_id": a.id, "kb_id": other_kb.id}),
                 ("photo_template", {"class_id": a.id, "kb_id": 999999})]
        ids = []
        for kind, payload in specs:
            job = jobs.enqueue(db, kind, payload)
            job.status = "failed"; job.error = "secret=do-not-expose"
            ids.append(job.id)
        db.commit()
        ctx = auth.AccessContext(teacher=teacher)
    app = FastAPI(); app.include_router(router)
    def database():
        with factory() as db:
            yield db
            db.commit()
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_teacher] = lambda: ctx
    with TestClient(app) as client:
        yield client, ids, factory
    engine.dispose()


def test_owner_can_read_and_retry_without_exposing_internal_error(scoped):
    client, ids, _ = scoped
    for index in [0, 2]:
        response = client.get(f"/jobs/{ids[index]}")
        assert response.status_code == 200
        assert "do-not-expose" not in response.text
        assert client.post(f"/jobs/{ids[index]}/retry").json()["status"] == "queued"
        assert client.post(f"/jobs/{ids[index]}/retry").status_code == 409


def test_other_class_subject_and_orphan_jobs_are_denied(scoped):
    client, ids, _ = scoped
    for index, expected in [(1, 403), (3, 403), (4, 403), (5, 404), (6, 404), (7, 403), (8, 404)]:
        assert client.get(f"/jobs/{ids[index]}").status_code == expected
        assert client.post(f"/jobs/{ids[index]}/retry").status_code == expected
