"""题库飞轮（improvement-plan §6）：提交考试时有标注题目沉淀到 bank_question。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.ingestion.commit import commit_exam
from app.models import BankQuestion, ExamResponse, ResponseAnswer
from tests.conftest import make_exam


def _answer(session, tpl, student_id, per_q):
    resp = ExamResponse(
        exam_template_id=tpl.id, student_id=student_id, source="excel", status="待审核"
    )
    session.add(resp)
    session.flush()
    for q in tpl.questions:
        session.add(
            ResponseAnswer(
                exam_response_id=resp.id,
                template_question_id=q.id,
                score=per_q.get(q.idx, 0.0),
            )
        )
    resp.total_score = sum(per_q.values())
    session.flush()
    return resp


def test_bank_flywheel_seeds_on_commit(session, env):
    """commit 时有标注题目入库,无标注不入;再次 commit 幂等不重复。"""
    p1, p2 = env["kp"]["P1"], env["kp"]["P2"]
    tpl = make_exam(
        session,
        env["class"].id,
        "飞轮卷",
        date(2025, 10, 1),
        "单元",
        [
            (1, 10.0, "解答", "应用", [(p1, 1.0)]),  # 有标注 -> 入库
            (2, 10.0, "解答", "应用", [(p2, 1.0)]),  # 有标注 -> 入库
            (3, 10.0, "解答", "应用", []),  # 无标注 -> 不入
        ],
    )
    _answer(session, tpl, env["students"]["T01"], {1: 8.0, 2: 6.0, 3: 5.0})

    result = commit_exam(session, tpl.id)
    assert result.bank_questions == 2

    rows = list(session.scalars(select(BankQuestion).order_by(BankQuestion.id)))
    assert len(rows) == 2
    tagged_ids = {q.id for q in tpl.questions if q.idx in (1, 2)}
    assert {r.source_template_question_id for r in rows} == tagged_ids
    assert all(r.status == "已审核" for r in rows)
    assert all(r.kb_version_id == env["kb"].id for r in rows)  # 从标注 kp 反查

    # 幂等：再次 commit 不重复入库
    result2 = commit_exam(session, tpl.id)
    assert result2.bank_questions == 0
    assert len(list(session.scalars(select(BankQuestion)))) == 2
