"""P25 判据结构性误报度量（effectiveness-validation-plan V4）。

「低于班级 P25」作为独立薄弱判据，按定义约 25% 学生恒满足--即使全班都达标。
本测试度量该结构性误报，并文档化与「不展示排名（双减）」约束的张力。
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.db import Base
from app.kb.graph import KpGraph
from app.models import (
    Class,
    ExamResponse,
    KbVersion,
    KnowledgePoint,
    ResponseAnswer,
    School,
    Student,
    TemplateQuestion,
    QuestionKp,
    ExamTemplate,
    TeachingProgress,
)
from app.ingestion.commit import commit_exam
from app.pipeline.weakness import assess_student_kps


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    s = S()
    yield s
    s.close()


def test_p25_structural_false_positive(session, monkeypatch):
    """全班均高于底线（0.6）时，P25 判据仍误报约 25% 学生薄弱（standard 模式）。"""
    monkeypatch.setattr("app.pipeline.weakness.WEAKNESS_MODE", "standard")
    kb = KbVersion(subject="数学", textbook_edition="测试版", version="t")
    session.add(kb)
    session.flush()
    # 6 个独立 kp，floor 0.6
    kp_ids: dict[str, int] = {}
    for code in ["K1", "K2", "K3", "K4", "K5", "K6"]:
        kp = KnowledgePoint(kb_version_id=kb.id, code=code, name=code, grade=7, semester=1,
                            chapter="测试章", cog_levels_expected=["应用"],
                            difficulty_prior=0.5, mastery_floor=0.6)
        session.add(kp)
        session.flush()
        kp_ids[code] = kp.id

    school = School(name="测试学校")
    session.add(school)
    session.flush()
    clazz = Class(school_id=school.id, name="测试班", grade=7)
    session.add(clazz)
    session.flush()
    for kid in kp_ids.values():
        session.add(TeachingProgress(class_id=clazz.id, kp_id=kid, taught_at=date(2025, 9, 1)))

    # 8 学生，目标掌握度全部高于 floor(0.6)，均匀分布在 [0.70, 0.95]
    targets = [0.70, 0.74, 0.78, 0.82, 0.86, 0.90, 0.92, 0.95]
    stu_ids: list[int] = []
    for i, t in enumerate(targets, start=1):
        stu = Student(school_id=school.id, class_id=clazz.id, name_or_alias=f"S{i:02d}")
        session.add(stu)
        session.flush()
        stu_ids.append(stu.id)

    # 3 场考试，每个 kp 一道题(full 10)，学生得分 = target*10 -> value=target
    for ei, d in enumerate([date(2025, 9, 30), date(2025, 11, 10), date(2026, 1, 15)]):
        tpl = ExamTemplate(class_id=clazz.id, name=f"考{ei}", exam_date=d, type="期中")
        session.add(tpl)
        session.flush()
        qids = []
        for idx, code in enumerate(kp_ids, start=1):
            tq = TemplateQuestion(exam_template_id=tpl.id, idx=idx, stem=f"题{idx}",
                                  q_type="解答", full_score=10.0, cog_level="应用")
            session.add(tq)
            session.flush()
            session.add(QuestionKp(template_question_id=tq.id, kp_id=kp_ids[code], weight=1.0))
            qids.append(tq.id)
        for sid, t in zip(stu_ids, targets):
            resp = ExamResponse(exam_template_id=tpl.id, student_id=sid, source="excel", status="待审核")
            session.add(resp)
            session.flush()
            score = round(t * 10 * 2) / 2
            for qid in qids:
                session.add(ResponseAnswer(exam_response_id=resp.id, template_question_id=qid, score=score))
            resp.total_score = round(score * len(qids), 2)
    session.flush()
    for tpl in session.scalars(select(ExamTemplate)):
        commit_exam(session, tpl.id)

    graph = KpGraph(session, kb.id)
    as_of = datetime(2026, 1, 16, 12, 0)

    # 统计每个学生的薄弱判定
    total_assessed = 0
    weak_floor = 0          # 绝对底线 / 两者（低于 floor）
    weak_p25_only = 0       # 仅班级P25（高于 floor 但低于 P25）
    for sid in stu_ids:
        for a in assess_student_kps(session, graph, sid, clazz.id, as_of):
            if a.gate is not None or a.mastery is None:
                continue
            total_assessed += 1
            if a.is_weak:
                if a.weak_criterion in ("绝对底线", "两者"):
                    weak_floor += 1
                elif a.weak_criterion == "班级P25":
                    weak_p25_only += 1

    fp_rate = weak_p25_only / total_assessed if total_assessed else 0.0
    print(
        f"\n[V4] 全班高于底线：评估 {total_assessed} 项，"
        f"绝对底线误报 {weak_floor}，班级P25误报 {weak_p25_only}（率 {fp_rate:.2f}）"
    )
    # 结构性误报：全班都达标，仍有约 25% 被 P25 判薄弱
    assert weak_floor == 0, "全班高于底线，不应有绝对底线误报"
    assert weak_p25_only > 0, "P25 判据应产生结构性误报（全班达标仍判薄弱）"
    assert 0.15 <= fp_rate <= 0.35, f"P25 结构性误报率 {fp_rate:.2f} 偏离预期 ~0.25"
