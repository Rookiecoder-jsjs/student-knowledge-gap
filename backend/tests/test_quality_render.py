"""候选3：班级质量报告渲染层纯函数（quality_render.py）。无 DB、无 LLM。

覆盖：空数据 / 低得分率标注 / 共性薄弱建议 / 未标注题提醒 / 快照契约。
"""

from __future__ import annotations

from app.reports.quality_model import QualityReportModel
from app.reports.quality_render import model_to_snapshot, render_quality_markdown


def _model(**kw) -> QualityReportModel:
    base = dict(
        class_name="七(1)班",
        exam_name="期中",
        exam_type="单元",
        exam_date="2025-11-02",
        committed=2,
        pending=1,
        totals=[80.0, 60.0],
        full_total=100.0,
        question_rates=[],
        kp_stats={},
        common_weak=[],
    )
    base.update(kw)
    return QualityReportModel(**base)


def test_render_header_totals_and_pending():
    md = render_quality_markdown(_model())
    assert "考后质量分析：期中" in md
    assert "七(1)班" in md and "2025-11-02" in md and "单元" in md
    assert "2 / 3（待审核 1 人）" in md
    assert "班级平均 70.0 分" in md and "最高 80" in md and "最低 60" in md


def test_render_empty_totals():
    md = render_quality_markdown(_model(totals=[], committed=0, pending=3))
    assert "尚无已提交的作答数据" in md


def test_render_low_rate_and_untagged():
    model = _model(question_rates=[
        {"idx": 1, "q_type": "选择", "full_score": 5, "rate": 0.3,
         "kps": "绝对值", "low": True},
        {"idx": 2, "q_type": "解答", "full_score": 10, "rate": None,
         "kps": "", "low": False},
    ])
    md = render_quality_markdown(model)
    assert "30%" in md and "⚠️ 低得分率" in md
    assert "未标注" in md
    assert "题目 [2] 未标注知识点" in md


def test_render_common_weak_section():
    model = _model(common_weak=[
        {"code": "M7A-105", "name": "绝对值", "class_avg": 0.4, "weak_share": 0.8, "n": 4},
    ])
    md = render_quality_markdown(model)
    assert "绝对值" in md and "待加强占比 80%" in md
    assert "集体教学" in md
    assert "未发现达到共性标准的班级待加强点" not in md


def test_render_no_common_weak():
    md = render_quality_markdown(_model())
    assert "未发现达到共性标准的班级待加强点" in md


def test_snapshot_contract():
    snap = model_to_snapshot(_model())
    assert snap["stats"] == {"mean": 70.0, "max": 80.0, "min": 60.0}
    assert snap["committed"] == 2 and snap["pending"] == 1
    assert snap["question_rates"] == [] and snap["common_weak"] == []
    assert snap["class"] == "七(1)班" and snap["exam_date"] == "2025-11-02"
