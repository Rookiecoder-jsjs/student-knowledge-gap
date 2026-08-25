"""写工具测试（Phase 3 批次B，agent-product-design §5.1/§5.3）。

审批门语义是核心断言：create_report_draft 产 draft（收件箱可见、可签发），
record_intervention 产 suggested（行动明细可确认）——Agent 永不直接落终态。
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app import inbox
from app.db import Base
from app.kb.graph import KpGraph
from app.mcp_tools import ToolInputError, create_report_draft, record_intervention
from app.models import (
    Class,
    ExamTemplate,
    Intervention,
    KbVersion,
    KnowledgePoint,
    Report,
    School,
    Student,
    TeachingProgress,
)


@pytest.fixture()
def env(session):
    school = School(name="学校")
    session.add(school)
    session.flush()
    kb = KbVersion(subject="数学", textbook_edition="t", version="1")
    session.add(kb)
    session.flush()
    kp = KnowledgePoint(
        kb_version_id=kb.id, code="P1", name="基础点", grade=7, semester=1,
        chapter="章", cog_levels_expected=["应用"], difficulty_prior=0.5,
        mastery_floor=0.6,
    )
    session.add(kp)
    session.flush()
    clazz = Class(school_id=school.id, name="一班", grade=7, subject="数学")
    session.add(clazz)
    session.flush()
    stu = Student(school_id=school.id, class_id=clazz.id, name_or_alias="小A")
    session.add(stu)
    session.flush()
    session.add(TeachingProgress(class_id=clazz.id, kp_id=kp.id, taught_at=date(2025, 9, 1)))
    tpl = ExamTemplate(
        class_id=clazz.id, name="单元测", exam_date=date(2025, 10, 15), type="单元",
    )
    session.add(tpl)
    session.flush()
    graph = KpGraph(session, kb.id)
    return {"graph": graph, "class": clazz, "student": stu, "kp_id": kp.id,
            "exam": tpl}


# ---------------------------------------------------------------------------
# create_report_draft
# ---------------------------------------------------------------------------


def test_create_report_draft_enters_inbox_as_draft(session, env):
    r = create_report_draft(
        session, env["graph"], report_type="student_diagnosis",
        student_id=env["student"].id, markdown="# 小A 的诊断\n进步与下一步……",
    )
    assert r["status"] == "draft"
    report = session.get(Report, r["report_id"])
    assert report.status == "draft"
    # 收件箱可见，且能走签发状态机到 issued
    drafts = inbox.list_drafts(session)
    assert drafts["total"] == 1 and drafts["items"][0]["report_id"] == report.id
    out = inbox.transition(session, report.id, "issue", note=None)
    assert out["status"] == "issued"


def test_create_report_draft_reject_rejects_bad_input(session, env):
    with pytest.raises(ToolInputError):
        create_report_draft(
            session, env["graph"], report_type="quality_analysis",  # 不开放给 Agent
            class_id=env["class"].id, markdown="x",
        )
    with pytest.raises(ToolInputError):
        create_report_draft(
            session, env["graph"], report_type="student_diagnosis",
            student_id=env["student"].id, markdown="   ",
        )
    with pytest.raises(LookupError):
        create_report_draft(
            session, env["graph"], report_type="student_diagnosis",
            student_id=99999, markdown="x",
        )


def test_create_report_draft_student_class_mismatch(session, env):
    other = Class(school_id=env["class"].school_id, name="二班", grade=7, subject="数学")
    session.add(other)
    session.flush()
    with pytest.raises(ToolInputError):
        create_report_draft(
            session, env["graph"], report_type="student_diagnosis",
            class_id=other.id, student_id=env["student"].id, markdown="x",
        )


# ---------------------------------------------------------------------------
# record_intervention
# ---------------------------------------------------------------------------


def test_record_intervention_creates_suggested_row(session, env):
    r = record_intervention(
        session, env["graph"],
        student_id=env["student"].id, kp_code="P1", kind="spaced_review",
        exam_id=env["exam"].id, note="调查后建议间隔复习",
    )
    assert r["status"] == "suggested" and r["kind_label"]
    row = session.get(Intervention, r["intervention_id"])
    assert row.status == "suggested" and row.scope == "student"
    assert row.class_id == env["class"].id and row.student_id == env["student"].id


def test_record_intervention_validates_kind(session, env):
    with pytest.raises(ToolInputError):
        record_intervention(
            session, env["graph"],
            student_id=env["student"].id, kp_code="P1", kind="魔法补习",
            exam_id=env["exam"].id,
        )


def test_record_intervention_requires_taught_kp(session, env):
    kb_id = session.scalar(select(KbVersion.id))
    kp2 = KnowledgePoint(
        kb_version_id=kb_id, code="U9", name="未教点", grade=7, semester=1,
        chapter="章", cog_levels_expected=["应用"], difficulty_prior=0.5,
        mastery_floor=0.6,
    )
    session.add(kp2)
    session.flush()
    with pytest.raises(ToolInputError):
        record_intervention(
            session, env["graph"],
            student_id=env["student"].id, kp_code="U9", kind="spaced_review",
            exam_id=env["exam"].id,
        )
