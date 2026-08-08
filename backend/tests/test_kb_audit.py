"""图谱可疑边反查（improvement-plan §2.2）。"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.ingestion.commit import commit_exam
from app.kb.graph import KpGraph, _pearson
from app.models import ExamResponse, ResponseAnswer
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


def test_pearson():
    assert _pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)  # 完全正相关
    assert _pearson([1, 2, 3], [6, 4, 2]) == pytest.approx(-1.0)  # 完全反相关
    assert abs(_pearson([0, 0, 1, 1], [0, 1, 0, 1])) < 1e-9  # 无关 -> 0
    assert _pearson([1, 1, 1], [2, 3, 4]) is None  # 零方差
    assert _pearson([1], [2]) is None  # 样本不足


def test_suspect_edges_detects_uncorrelated(session, env):
    """P1->P2 两端掌握度无关(corr≈0)-> 可疑;P2->P3 完全正相关(corr=1)-> 不可疑。

    每生每 kp 三场得分全相同 -> 掌握度精确=得分率,相关性可控、稳定。
    """
    p1, p2, p3 = env["kp"]["P1"], env["kp"]["P2"], env["kp"]["P3"]
    sids = [env["students"][f"T0{i}"] for i in range(1, 5)]  # T01..T04

    # (P1, P2, P3) 三场得分(满分 10,全相同)-> 掌握度 0.1/0.9
    #   P1=[.1,.1,.9,.9]  P2=[.1,.9,.1,.9]  -> corr=0  可疑
    #   P3=[.1,.9,.1,.9] = P2               -> corr=1  不可疑
    mastery = {
        sids[0]: (1.0, 1.0, 1.0),
        sids[1]: (1.0, 9.0, 9.0),
        sids[2]: (9.0, 1.0, 1.0),
        sids[3]: (9.0, 9.0, 9.0),
    }
    for i in range(3):
        tpl = make_exam(
            session,
            env["class"].id,
            f"E{i}",
            date(2025, 10, 1 + i * 10),
            "单元",
            [
                (1, 10.0, "解答", "应用", [(p1, 1.0)]),
                (2, 10.0, "解答", "应用", [(p2, 1.0)]),
                (3, 10.0, "解答", "应用", [(p3, 1.0)]),
            ],
        )
        for sid, (s1, s2, s3) in mastery.items():
            _answer(session, tpl, sid, {1: s1, 2: s2, 3: s3})
        commit_exam(session, tpl.id)

    graph = KpGraph(session, env["kb"].id)
    when = datetime(2025, 11, 1, 12, 0)

    # 样本门槛高于学生数 -> 全过滤
    assert graph.suspect_edges(env["class"].id, when, min_samples=5) == []

    edges = graph.suspect_edges(env["class"].id, when, min_samples=4, corr_threshold=0.3)
    pairs = {(e["from_code"], e["to_code"]) for e in edges}
    assert ("P1", "P2") in pairs  # 无关 -> 可疑
    assert ("P2", "P3") not in pairs  # 完全正相关 -> 不可疑

    p1p2 = next(e for e in edges if (e["from_code"], e["to_code"]) == ("P1", "P2"))
    assert abs(p1p2["corr"]) < 0.1  # 可疑边相关接近 0
    assert p1p2["n"] == 4
    assert {"from_code", "from_name", "to_code", "to_name", "weight", "n", "corr"} <= p1p2.keys()
