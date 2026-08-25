"""候选4：commit_exam seam 显式化。

- commit 不再触发报告生成（只做状态机 + 派生证据 + 题库飞轮）；
- 报告生成是调用方的组合步骤（与 API commit 端点同构）：
  commit 后显式 ``generate_exam_reports`` 才落报告。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.ingestion.commit import add_manual_response, commit_exam
from app.models import Report
from app.reports.auto_generate import generate_exam_reports
from tests.conftest import add_progress, make_exam


def test_commit_alone_creates_no_reports(session, env):
    """只 commit（不组合 generate_exam_reports）→ 不产生任何报告行。"""
    kp = env["kp"]["P1"]
    tpl = make_exam(
        session, env["class"].id, "仅提交", date(2025, 10, 10), "单元",
        [(1, 10.0, "解答", "应用", [(kp, 1.0)]),
         (2, 5.0, "选择", "识记", [(kp, 1.0)])],
    )
    add_progress(session, env["class"].id, [kp])
    add_manual_response(session, tpl.id, env["students"]["T01"], {1: 8.0, 2: 5.0})
    result = commit_exam(session, tpl.id)
    session.flush()

    assert result.committed_responses == 1
    assert result.quality_report is False and result.diagnoses == 0
    assert session.scalars(select(Report)).all() == [], "commit 本身不生成报告"


def test_commit_then_explicit_generate_reports(session, env):
    """组合步骤：commit 后显式 generate_exam_reports → 报告生成（seam 显式化）。"""
    kp = env["kp"]["P1"]
    tpl = make_exam(
        session, env["class"].id, "组合生成", date(2025, 10, 10), "单元",
        [(1, 10.0, "解答", "应用", [(kp, 1.0)])],
    )
    add_progress(session, env["class"].id, [kp])
    add_manual_response(session, tpl.id, env["students"]["T01"], {1: 8.0})
    result = commit_exam(session, tpl.id)
    reports = generate_exam_reports(session, tpl.id)
    session.flush()

    assert result.committed_responses == 1
    assert reports.quality is True and reports.diagnoses == 1
    # 质量报告 + 班级改进意见 + 学生诊断单 + 学生改进单（干预闭环新增第四类型）
    assert len(session.scalars(select(Report)).all()) == 4
