"""候选2：classes_overview 聚合纯函数（不经 HTTP，直接查库）。

覆盖：待办考试数（pending / 未审题）、最近一场考试状态、进度覆盖（与分析层同分母）、
active kb 缺失（空 grade7 集）时 progress 兜底 {0, 0}。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select

from app.ingestion.commit import add_manual_response, commit_exam
from app.models import QuestionKp, TemplateQuestion
from app.queries.classes_overview import classes_overview
from tests.conftest import add_progress, make_exam


def _review_all(session, tpl_id):
    """把一场考试的全部题目标签标记为已审（不再计作 todo）。"""
    for qk in session.scalars(
        select(QuestionKp)
        .join(TemplateQuestion, QuestionKp.template_question_id == TemplateQuestion.id)
        .where(TemplateQuestion.exam_template_id == tpl_id)
    ).all():
        qk.reviewed_at = datetime.utcnow()
    session.flush()


def test_overview_aggregation(session, env):
    """待办数/最近考试/进度覆盖一次聚合。"""
    kp1, kp2 = env["kp"]["P1"], env["kp"]["P2"]
    # 考试 A（较早）：待审核作答 + 未审题 → 计待办
    tpl_a = make_exam(
        session, env["class"].id, "月考A", date(2025, 11, 10), "单元",
        [(1, 10.0, "解答", "应用", [(kp1, 1.0)])],
    )
    add_manual_response(session, tpl_a.id, env["students"]["T01"], {1: 6.0})
    # 考试 B（较晚）：已提交 + 全部已审 → 不计待办，作为最近考试
    tpl_b = make_exam(
        session, env["class"].id, "月考B", date(2025, 12, 10), "单元",
        [(1, 10.0, "解答", "应用", [(kp2, 1.0)])],
    )
    add_manual_response(session, tpl_b.id, env["students"]["T01"], {1: 8.0})
    commit_exam(session, tpl_b.id)
    _review_all(session, tpl_b.id)
    add_progress(session, env["class"].id, [kp1, kp2])
    session.flush()

    result = classes_overview(session, {kp1, kp2})
    item = result["classes"][0]
    assert item["student_count"] == len(env["students"])
    assert item["exam_count"] == 2
    assert item["todo_count"] == 1, "仅考试 A 有待办（待审核+未审题）"
    assert item["latest_exam"]["exam_id"] == tpl_b.id
    assert item["latest_exam"]["submitted"] == 1 and item["latest_exam"]["pending"] == 0
    assert item["progress"] == {"taught": 2, "total": 2}


def test_overview_progress_degrades_without_grade7_set(session, env):
    """active kb 缺失（空 grade7 集）→ progress 兜底 {0, 0}，不抛错。"""
    kp1 = env["kp"]["P1"]
    tpl = make_exam(
        session, env["class"].id, "无kb", date(2025, 11, 10), "单元",
        [(1, 10.0, "解答", "应用", [(kp1, 1.0)])],
    )
    add_manual_response(session, tpl.id, env["students"]["T01"], {1: 6.0})
    session.flush()

    result = classes_overview(session, set())
    assert result["classes"][0]["progress"] == {"taught": 0, "total": 0}
