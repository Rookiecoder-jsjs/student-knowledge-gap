"""金标干预场景（intervention-loop-design §8 验收 + §9 风险表「防静默退化」）。

合成学生端到端：植入薄弱 → 提交生成干预建议 → 教师确认 → 注入复测证据
（掌握度回升）→ 断言效果推导捕获 improved、提升率统计正确。

与 test_gold.py 同纪律：真实数据到来前用植入真值验证整条干预闭环管线。
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import models  # noqa: F401 注册模型
from app.ingestion.commit import commit_exam
from app.intervention import (
    generate_interventions,
    intervention_effect,
    intervention_summary,
)
from app.kb.graph import KpGraph
from app.kb.loader import import_kb
from app.models import (
    Class,
    ExamResponse,
    ExamTemplate,
    Intervention,
    QuestionKp,
    ResponseAnswer,
    School,
    Student,
    TeachingProgress,
    TemplateQuestion,
)
from app.reports.auto_generate import generate_exam_reports
from simulator.test_gold import KB_YAML

AS_OF = datetime(2025, 10, 20, 12, 0)


@pytest.fixture(scope="module")
def iv_env():
    """迷你班级：6 人、单 kp（有理数运算）3 人弱 → 共性成立。"""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    session = S()

    kb = import_kb(session, str(KB_YAML))
    school = School(name="合成学校")
    session.add(school)
    session.flush()
    clazz = Class(school_id=school.id, name="干预班", grade=7, subject="数学")
    session.add(clazz)
    session.flush()
    students: dict[str, int] = {}
    for i in range(1, 7):
        stu = Student(school_id=school.id, class_id=clazz.id,
                      name_or_alias=f"干预生{i:02d}")
        session.add(stu)
        session.flush()
        students[f"S{i:02d}"] = stu.id

    # 前置链：M7A-101 正数与负数（根源）→ M7A-102 有理数概念（薄弱点）
    graph = KpGraph(session, kb.id)
    root_id = graph.code("M7A-101")
    weak_kp_id = graph.code("M7A-102")
    session.add(TeachingProgress(class_id=clazz.id, kp_id=root_id,
                                 taught_at=date(2025, 9, 1)))
    session.add(TeachingProgress(class_id=clazz.id, kp_id=weak_kp_id,
                                 taught_at=date(2025, 9, 1)))
    session.flush()

    def _exam(name: str, d: date) -> ExamTemplate:
        tpl = ExamTemplate(class_id=clazz.id, name=name, exam_date=d, type="单元")
        session.add(tpl)
        session.flush()
        # 每场两题：一题根源、一题薄弱点（各过证据门槛）
        for idx, kp in ((1, root_id), (2, weak_kp_id)):
            tq = TemplateQuestion(exam_template_id=tpl.id, idx=idx, stem=f"题{idx}",
                                  q_type="解答", full_score=10.0, cog_level="应用")
            session.add(tq)
            session.flush()
            session.add(QuestionKp(template_question_id=tq.id, kp_id=kp, weight=1.0))
        session.flush()
        return tpl

    def _run(tpl: ExamTemplate, weak_names: set[str], score_weak=3.0, score_good=9.0):
        for name, sid in students.items():
            score = score_weak if name in weak_names else score_good
            resp = ExamResponse(exam_template_id=tpl.id, student_id=sid,
                                source="excel", status="待审核")
            session.add(resp)
            session.flush()
            for q in tpl.questions:
                session.add(ResponseAnswer(exam_response_id=resp.id,
                                           template_question_id=q.id,
                                           score=score))
            resp.total_score = score * len(tpl.questions)
        commit_exam(session, tpl.id)

    # 三场弱考试（过归因证据门槛）+ 提交自动生成（含干预建议尾步）
    for i in range(3):
        _run(_exam(f"基线E{i}", date(2025, 10, 2 + i * 4)), {"S01", "S02", "S03"})
    last = session.query(ExamTemplate).order_by(ExamTemplate.id.desc()).first()
    generate_exam_reports(session, last.id)
    session.commit()

    yield session, graph, clazz, students, root_id, weak_kp_id, _exam, _run
    session.close()
    engine.dispose()
    os.unlink(db_path)


def test_golden_intervention_loop(iv_env):
    """植入真值全链路：建议 → 确认 → 复测回升 → improved + 提升率正确。"""
    session, graph, clazz, students, root_id, weak_kp_id, _exam, _run = iv_env

    # ---- 建议已随提交生成：共性 reteach + 个体 prereq_backfill 行 ----
    rows = list(session.scalars(select(Intervention).where(
        Intervention.class_id == clazz.id)))
    assert rows, "提交后应有干预建议行"
    reteach = [r for r in rows if r.kind == "reteach"]
    assert len(reteach) >= 1 and reteach[0].scope == "class"
    backfills = [r for r in rows if r.kind == "prereq_backfill"]
    assert backfills, "根源同步低应产出回补建议"
    # 回补建议的目标是薄弱点 M7A-102（根源信息在行动视图的 note/group 语义中）
    assert all(r.kp_id == weak_kp_id for r in backfills)

    # ---- 教师确认一条个体行（S01 的回补建议） ----
    target = next(
        r for r in rows
        if r.student_id == students["S01"] and r.kind == "prereq_backfill"
    )
    target.status = "done"
    target.done_at = datetime(2025, 10, 21, 12, 0)
    session.flush()

    # ---- 无复测证据：awaiting_retest（分母口径保护） ----
    e0 = intervention_effect(session, graph, target.id)
    assert e0["effect_status"] == "awaiting_retest"
    summ0 = intervention_summary(session, graph, clazz.id)
    assert summ0["effects"]["awaiting_retest"] >= 1

    # ---- 注入复测证据：S01 回升到高分 ----
    retest = _exam("复测E", date(2025, 11, 5))
    _run(retest, {"S02"}, score_weak=4.0, score_good=9.0)  # S01 高分（不在弱名单）

    e1 = intervention_effect(session, graph, target.id)
    assert e1["effect_status"] == "improved", e1
    assert e1["post_mastery"] > e1["pre_mastery"]

    # ---- 北极星：可评估子集=1、全部 improved → lift_rate=1.0 ----
    summ = intervention_summary(session, graph, clazz.id)
    assert summ["evaluable_count"] >= 1
    assert summ["intervention_lift_rate"] == round(
        summ["effects"]["improved"] / summ["evaluable_count"], 3)
    assert summ["adoption_rate"] is not None


def test_golden_no_reflood_and_regen_idempotent(iv_env):
    """重跑幂等：suggested 刷新、done 保留；无新证据不重发（防轰炸）。"""
    session, graph, clazz, students, root_id, weak_kp_id, _exam, _run = iv_env

    done_ids = {
        r.id
        for r in session.scalars(select(Intervention).where(
            Intervention.class_id == clazz.id, Intervention.status == "done"))
    }
    n_before = len(list(session.scalars(select(Intervention).where(
        Intervention.class_id == clazz.id))))

    last = session.query(ExamTemplate).order_by(ExamTemplate.id.desc()).first()
    as_of = datetime.combine(last.exam_date, time(23, 59))
    generate_interventions(session, graph, clazz.id, last.id, as_of)

    rows_after = list(session.scalars(select(Intervention).where(
        Intervention.class_id == clazz.id)))
    ids_after = {r.id for r in rows_after}
    assert done_ids <= ids_after, "done 行跨重跑保留"

    # S01 的 done 行（复测已有证据且不再弱）不应被重复建议轰炸成两条同 kind suggested
    s01_sugg = [
        r for r in rows_after
        if r.student_id == students["S01"] and r.status == "suggested"
        and r.kind != "reteach"
    ]
    assert len(s01_sugg) <= 1, f"S01 不应有多条同点 suggested：{len(s01_sugg)}"
