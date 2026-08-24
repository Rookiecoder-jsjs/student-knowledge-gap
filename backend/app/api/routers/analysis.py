"""分析域路由：掌握度 / 薄弱 / 归因 / 诊断 / 质量报告 / 教师否决（候选2 拆分）。

分析类接口全部 derive-on-read（不变量②）；诊断 get-or-generate 编排在
``reports.diagnosis_orchestrator``（候选1/候选2），本文件只做解析与翻译。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import _active_kb, _as_dt, _graph, get_db
from app.kb.edit import log_correction
from app.models import Attribution, Class, Student
from app.pipeline.attribution import (
    attribution_closure,
    materialize_attribution_verdicts,
    verify_attribution_prediction,
)
from app.pipeline.mastery import mastery_at
from app.pipeline.weakness import assess_student_kps
from app.queries.diagnosis_sheet import class_diagnosis_sheet
from app.reports.diagnosis_orchestrator import (
    get_or_create_narrative,
    get_or_generate_diagnosis,
    get_or_generate_quality_report,
)
from app.schemas import AttributionOverride

router = APIRouter()


# ---------------------------------------------------------------------------
# 掌握度 / 薄弱 / 归因（derive-on-read）
# ---------------------------------------------------------------------------


@router.get("/students/{student_id}/mastery")
def student_mastery(student_id: int, as_of: date | None = None, db: Session = Depends(get_db)):
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    when = _as_dt(as_of)
    out = []
    for kp_id in graph.grade7_kp_ids():
        kp = graph.kp(kp_id)
        m = mastery_at(db, student_id, kp_id, when)
        if m is not None:
            out.append({"code": kp.code, "name": kp.name, "mastery": round(m, 3)})
    return {"student_id": student_id, "as_of": str(when.date()), "mastery": out}


@router.get("/students/{student_id}/weaknesses")
def student_weaknesses(student_id: int, as_of: date | None = None, db: Session = Depends(get_db)):
    stu = db.get(Student, student_id)
    if stu is None:
        raise HTTPException(404, "学生不存在")
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    when = _as_dt(as_of)
    assessments = assess_student_kps(db, graph, student_id, stu.class_id, when)
    return {
        "student_id": student_id,
        "as_of": str(when.date()),
        "weak": [
            {
                "code": a.kp_code,
                "name": a.kp_name,
                "mastery": round(a.mastery, 3) if a.mastery is not None else None,
                "criterion": a.weak_criterion,
                "evidence_count": a.evidence_count,
                "trajectory": a.trajectory,
                "stale": a.stale,
                "class_common": a.is_class_common,
            }
            for a in assessments
            if a.is_weak
        ],
        "gates": {
            "未学到": sum(1 for a in assessments if a.gate == "未学到"),
            "数据不足": sum(1 for a in assessments if a.gate == "数据不足"),
        },
    }


@router.post("/students/{student_id}/attributions")
def run_attributions(student_id: int, as_of: date | None = None, db: Session = Depends(get_db)):
    stu = db.get(Student, student_id)
    if stu is None:
        raise HTTPException(404, "学生不存在")
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    when = _as_dt(as_of)
    active = materialize_attribution_verdicts(db, graph, student_id, stu.class_id, when)
    return {
        "student_id": student_id,
        "attributions": [
            {
                "id": a.id,
                "kp": graph.kp(a.kp_id).name,
                "type": a.type,
                "confidence": a.confidence,
                "root_kp": graph.kp(a.root_kp_id).name if a.root_kp_id else None,
                "prediction": a.prediction,
                "status": a.status,
            }
            for a in active
        ],
    }


# ---------------------------------------------------------------------------
# 报告（get-or-generate 编排在领域层；数字模板注入、物化留档）
# ---------------------------------------------------------------------------


@router.get("/classes/{class_id}/quality-report")
def quality_report(
    class_id: int, exam_id: int, narrative: bool = False, db: Session = Depends(get_db)
):
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    # get-or-generate 编排在领域层（候选2 diagnosis_orchestrator）：不感知 HTTP
    try:
        report = get_or_generate_quality_report(db, graph, class_id, exam_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    markdown = report.content_markdown
    if narrative:
        markdown += get_or_create_narrative(db, report)
    # snapshot（概况页数据源，diagnosis-sheet-redesign §1.1）：已含
    # question_rates/common_weak/committed/stats，概况页一屏直接渲染，零新增计算端点。
    return {"report_id": report.id, "markdown": markdown, "snapshot": report.snapshot_json}


@router.get("/classes/{class_id}/diagnosis-sheet")
def class_diagnosis_sheet_endpoint(class_id: int, db: Session = Depends(get_db)):
    """班级诊断单聚合（diagnosis-sheet-redesign §1.2/B1）。

    滚动现状（跨考试 derive-on-read）+ 最新班级改进意见（LLM/模板）+
    行动与闭环摘要（intervention-loop 落地后接入，本期空占位）。
    """
    if db.get(Class, class_id) is None:
        raise HTTPException(404, "班级不存在")
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    return class_diagnosis_sheet(db, graph, class_id)


@router.get("/students/{student_id}/diagnosis")
def diagnosis(
    student_id: int,
    exam_id: int | None = None,
    as_of: date | None = None,
    narrative: bool = False,
    db: Session = Depends(get_db),
):
    """诊断 get-or-generate 三分支编排在领域层（候选2），本端点只做翻译与形状。"""
    if db.get(Student, student_id) is None:
        raise HTTPException(404, "学生不存在")
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    try:
        report, _generated = get_or_generate_diagnosis(
            db, graph, student_id, exam_id=exam_id, as_of=as_of
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    markdown = report.content_markdown
    if narrative:
        markdown += get_or_create_narrative(db, report)
    # 快照 as_of（诊断报告 snapshot 恒有该字段）：前端据此标注并同步右侧弱项面板
    as_of_str = (report.snapshot_json or {}).get("as_of")
    return {"report_id": report.id, "markdown": markdown, "as_of": as_of_str}


# ---------------------------------------------------------------------------
# 教师否决归因（教师否决权 — 引擎重跑永不复活 overridden）
# ---------------------------------------------------------------------------


@router.post("/attributions/{attribution_id}/override")
def override_attribution(attribution_id: int, req: AttributionOverride, db: Session = Depends(get_db)):
    att = db.get(Attribution, attribution_id)
    if att is None:
        raise HTTPException(404, "归因不存在")
    if att.status != "active":
        raise HTTPException(400, f"归因状态为 {att.status}，无需否决")
    att.status = "overridden"
    att.teacher_note = req.note or None
    log_correction(db, "attribution", attribution_id, "status", "active", "overridden", req.reviewer)
    return {"attribution_id": attribution_id, "status": "overridden", "note": att.teacher_note}


@router.post("/attributions/{attribution_id}/verify")
def verify_attribution(attribution_id: int, as_of: date | None = None, db: Session = Depends(get_db)):
    """诊断题证伪：用诊断证据验证前置缺陷归因预测（improvement-plan §1.4-A）。

    诊断题（type=诊断、单 kp）作答提交后派生单 kp 证据；本端点重查前置点掌握度：
    已达标 -> 证伪 -> 置 overridden（跨重跑保留）；仍低 -> 证实（保留 active）。
    """
    att = db.get(Attribution, attribution_id)
    if att is None:
        raise HTTPException(404, "归因不存在")
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    when = _as_dt(as_of)
    try:
        result = verify_attribution_prediction(db, graph, attribution_id, when)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result["status"] == "overridden":
        log_correction(
            db, "attribution", attribution_id, "status", "active", "overridden", "diagnostic"
        )
    return result


@router.get("/attributions/closure")
def attributions_closure(class_id: int | None = None, db: Session = Depends(get_db)):
    """证伪闭环度量（effectiveness-validation-plan V3-度量）。

    归因按状态/证伪结论的分布、诊断验证率、教师否决率。
    closure_rate = 被诊断题验证过的归因占比；低 = 「可证伪」停留在纸面。
    端点函数名与导入的领域函数 attribution_closure 刻意不同名——同名会
    shadow 导入、自调用无限递归（活体冒烟实测发现；该端点此前无测试覆盖）。
    """
    return attribution_closure(db, class_id)