"""学生改进单 + 质量报告§五行动方向测试（intervention-loop-design §2/§5）。

覆盖：改进单随提交自动生成且幂等；模板正文硬约束（≤3 改进项、无他人/班级数据、
无负面定性词）；plan_writer 学生版校验（结构段/禁词/条目数/班级泄漏）；
质量报告 §五 教学行动方向（actions 注入渲染、空 actions 不出节）。
"""

from __future__ import annotations

from datetime import date

from app.llm.plan_writer import _validate_student_action_plan
from app.models import ExamTemplate, Intervention, Report
from app.reports.auto_generate import generate_exam_reports
from app.reports.diagnosis_model import compute_diagnosis_model
from app.reports.quality_render import render_quality_markdown
from app.reports.student_action_plan import render_action_plan_markdown
from tests.conftest import add_progress, make_exam
from tests.test_intervention_model import (
    _bulk_answer,
    _commit,
    _exam,
    _run,
)


def _committed_class(session, env):
    """3/6 人弱 → 共性成立的最小提交环境。"""
    add_progress(session, env["class"].id, [env["kp"]["P1"]])
    for i in range(2):
        _run(session, env,
             _exam(session, env, f"E{i}", date(2025, 10, 5 + i * 10), "P1"),
             {"T01", "T02", "T03"})
    return session.query(ExamTemplate).order_by(ExamTemplate.id.desc()).first()


def test_action_plan_generated_and_idempotent(session, env):
    """提交后每生一份改进单；重复生成替换不重复。"""
    tpl = _committed_class(session, env)
    generate_exam_reports(session, tpl.id)
    plans = session.query(Report).filter_by(type="student_action_plan").all()
    assert len(plans) == 6, "每个已提交学生一份改进单"

    generate_exam_reports(session, tpl.id)
    assert len(session.query(Report).filter_by(type="student_action_plan").all()) == 6, \
        "幂等替换"


def test_action_plan_content_constraints(session, env):
    """模板正文硬约束：改进项 ≤3、无班级统计泄漏、无负面定性词。"""
    # 4 个弱 kp 会超 3 条上限——构造多弱环境验证截断
    kps = ["P1", "P2", "P3", "U"]
    add_progress(session, env["class"].id, [env["kp"][c] for c in kps])
    for c in kps:
        for i in range(3):
            _run(session, env,
                 _exam(session, env, f"{c}E{i}", date(2025, 10, 1 + i), c),
                 {"T01", "T02", "T03"})
    from app.kb.graph import KpGraph
    graph = KpGraph(session, env["kb"].id)
    from datetime import datetime, time
    as_of = datetime.combine(date(2025, 10, 31), time(23, 59))
    model = compute_diagnosis_model(
        session, graph, env["students"]["T01"], as_of=as_of
    )
    md = render_action_plan_markdown(graph, model)

    assert "你已经在进步的" in md and "你的下一步" in md and "学习方法小建议" in md
    # 改进项 ≤3：### 小节在「你的下一步」与「学习方法小建议」之间
    # （split 残留：后界标题行会剩下 "### " 孤行，须剔除）
    plan_seg = md.split("你的下一步", 1)[-1].split("学习方法小建议", 1)[0]
    n_items = sum(
        1 for ln in plan_seg.splitlines()
        if ln.strip().startswith("###") and ln.strip() != "###"
    )
    assert n_items <= 3, f"行动卡改进项应 ≤3，实际 {n_items}"
    for banned in ("薄弱", "排名"):
        assert banned not in md
    # 无班级统计泄漏（证据包的班级字段不得转述进学生版）
    assert "班级" not in md


def test_action_plan_validator_rejects_bad_output():
    """LLM 输出校验：缺结构段/超条数/禁词/班级统计泄漏均拒绝。"""
    good = (
        "### 你已经在进步的\n- 有理数：保持得不错。\n\n"
        "### 你的下一步\n### 整式\n- 现状：掌握约 55%。\n- 怎么做：先补基础点。\n"
        "- 什么时候：本周内。\n\n### 学习方法小建议\n- 卡住时先回头补基础。"
    )
    assert _validate_student_action_plan(good)

    no_seg = good.replace("### 学习方法小建议", "### 小贴士")
    assert not _validate_student_action_plan(no_seg)

    four_items = good.replace("### 学习方法小建议",
                              "### 第二点\n### 第三点\n### 第四点\n### 学习方法小建议")
    assert not _validate_student_action_plan(four_items)

    banned_word = good.replace("先补基础点", "你太差了")
    assert not _validate_student_action_plan(banned_word)

    leak = good.replace("掌握约 55%", "班级平均 60%，你 40%")
    assert not _validate_student_action_plan(leak)

    rank = good.replace("保持得不错", "你是第 1 名")
    assert not _validate_student_action_plan(rank)


def test_quality_report_section_five_actions(session, env):
    """actions 注入 → §五教学行动方向渲染；snapshot.actions 同步。"""
    tpl = _committed_class(session, env)
    result = generate_exam_reports(session, tpl.id)

    quality = session.query(Report).filter_by(
        exam_id=tpl.id, type="quality_analysis").one()
    md = quality.content_markdown
    snapshot = quality.snapshot_json

    if result.interventions > 0:
        assert "## 五、教学行动方向" in md
        assert snapshot["actions"], "有建议行则 actions 摘要非空"
        assert all("kind" in a and "scope" in a for a in snapshot["actions"])
        # 全班 reteach 行应在头部（杠杆序）
        kinds = [a["kind"] for a in snapshot["actions"]]
        assert "reteach" in kinds
    else:
        assert "## 五" not in md or "教学行动方向" not in md

    # 干预行确实落库（共性 3/6 ≥ 0.4）
    reteach = session.query(Intervention).filter_by(kind="reteach").all()
    assert reteach, "共性薄弱应有全班重讲行"


def test_quality_report_no_actions_no_section_five(session, env):
    """get-or-generate 路径不传 actions → 不出 §五（兼容旧调用）。"""
    from app.reports.quality_analysis import generate_quality_analysis
    from app.kb.graph import KpGraph

    tpl = _committed_class(session, env)
    graph = KpGraph(session, env["kb"].id)
    report = generate_quality_analysis(session, graph, env["class"].id, tpl.id)
    assert "教学行动方向" not in report.content_markdown
    assert "## 六、注意事项" in report.content_markdown
