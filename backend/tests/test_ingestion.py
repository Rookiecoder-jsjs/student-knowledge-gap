"""采集层：Excel 导入 / 手工录入 / 提交状态机（DESIGN §5）。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.ingestion.commit import add_manual_response, commit_exam
from app.ingestion.excel import import_excel
from app.models import EvidenceEvent, ExamResponse
from tests.conftest import make_exam


def _write_excel(path: Path, rows):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)


def test_excel_import_and_commit(session, env, tmp_path):
    kp = env["kp"]["P1"]
    tpl = make_exam(session, env["class"].id, "月考", date(2025, 10, 10), "单元",
                    [(1, 10.0, "解答", "应用", [(kp, 1.0)]),
                     (2, 5.0, "选择", "识记", [(kp, 1.0)])])
    f = tmp_path / "scores.xlsx"
    _write_excel(f, [
        ["姓名", "Q1", "Q2", "总分"],
        ["T01", 8, 5, 13],
        ["T02", 4, 0, 4],
        ["不存在的人", 1, 1, 2],
    ])
    result = import_excel(session, tpl.id, f)
    assert result.imported == 2
    assert result.unmatched_students and "不存在的人" in result.unmatched_students[0]

    # 未提交前不得有证据（不变量①）
    assert session.scalar(select(EvidenceEvent.id)) is None

    commit_result = commit_exam(session, tpl.id)
    assert commit_result.committed_responses == 2
    assert commit_result.evidence_events == 4  # 2 人 × 2 题 × 1 kp
    statuses = set(session.scalars(select(ExamResponse.status)))
    assert statuses == {"已提交"}


def test_excel_total_mismatch_warning(session, env, tmp_path):
    kp = env["kp"]["P1"]
    tpl = make_exam(session, env["class"].id, "月考2", date(2025, 10, 11), "单元",
                    [(1, 10.0, "解答", "应用", [(kp, 1.0)])])
    f = tmp_path / "bad.xlsx"
    _write_excel(f, [["姓名", "Q1", "总分"], ["T01", 8, 99]])
    result = import_excel(session, tpl.id, f)
    assert any("不一致" in w for w in result.warnings)
    resp = session.scalar(select(ExamResponse))
    assert resp.total_score == 8.0  # 以逐题求和为准


def test_manual_entry_validation(session, env):
    kp = env["kp"]["P1"]
    tpl = make_exam(session, env["class"].id, "手录", date(2025, 10, 12), "练习",
                    [(1, 10.0, "解答", "应用", [(kp, 1.0)])])
    with pytest.raises(ValueError, match="越界"):
        add_manual_response(session, tpl.id, env["students"]["T01"], {1: 12.0})
    resp = add_manual_response(session, tpl.id, env["students"]["T01"], {1: 7.0})
    assert resp.status == "待审核"
    assert resp.total_score == 7.0
