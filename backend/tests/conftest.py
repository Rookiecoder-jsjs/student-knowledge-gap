"""测试夹具：内存库 + 迷你知识库（P1 → P2 → P3 前置链 + 独立点 U）。"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.db import Base
from app.models import (
    Class,
    ExamTemplate,
    KbVersion,
    KnowledgePoint,
    KpRelation,
    QuestionKp,
    School,
    Student,
    TeachingProgress,
    TemplateQuestion,
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    s = S()
    yield s
    s.close()


@pytest.fixture()
def env(session):
    """kb + school/class + 知识点映射。"""
    kb = KbVersion(subject="数学", textbook_edition="测试版", version="t")
    session.add(kb)
    session.flush()

    specs = [("P1", "基础点", 0.6), ("P2", "中间点", 0.6), ("P3", "应用点", 0.6),
             ("U", "独立点", 0.6)]
    kp_ids: dict[str, int] = {}
    for code, name, floor in specs:
        kp = KnowledgePoint(
            kb_version_id=kb.id, code=code, name=name, grade=7, semester=1,
            chapter="测试章", cog_levels_expected=["应用"], difficulty_prior=0.5,
            mastery_floor=floor,
        )
        session.add(kp)
        session.flush()
        kp_ids[code] = kp.id

    session.add(KpRelation(from_kp_id=kp_ids["P1"], to_kp_id=kp_ids["P2"],
                           type="prerequisite", weight=0.9))
    session.add(KpRelation(from_kp_id=kp_ids["P2"], to_kp_id=kp_ids["P3"],
                           type="prerequisite", weight=0.9))

    school = School(name="测试学校")
    session.add(school)
    session.flush()
    clazz = Class(school_id=school.id, name="测试班", grade=7)
    session.add(clazz)
    session.flush()

    students: dict[str, int] = {}
    for i in range(1, 7):
        stu = Student(school_id=school.id, class_id=clazz.id, name_or_alias=f"T{i:02d}")
        session.add(stu)
        session.flush()
        students[f"T{i:02d}"] = stu.id

    session.commit()
    return {"kb": kb, "kp": kp_ids, "class": clazz, "students": students}


def make_exam(session, class_id, name, exam_date, type_, questions):
    """questions: [(idx, full_score, q_type, cog_level, [(kp_code→id, weight)])]"""
    tpl = ExamTemplate(class_id=class_id, name=name, exam_date=exam_date, type=type_)
    session.add(tpl)
    session.flush()
    for idx, full, q_type, cog, tags in questions:
        tq = TemplateQuestion(
            exam_template_id=tpl.id, idx=idx, stem=f"题{idx}", q_type=q_type,
            full_score=full, cog_level=cog,
            n_options=4 if q_type == "选择" else None,
        )
        session.add(tq)
        session.flush()
        for kp_id, w in tags:
            session.add(QuestionKp(template_question_id=tq.id, kp_id=kp_id, weight=w))
    session.flush()
    return tpl


def add_progress(session, class_id, kp_ids, taught_at=date(2025, 9, 1)):
    for kp_id in kp_ids:
        session.add(TeachingProgress(class_id=class_id, kp_id=kp_id, taught_at=taught_at))
    session.flush()


def dt(d: date) -> datetime:
    return datetime.combine(d, time(12, 0))
