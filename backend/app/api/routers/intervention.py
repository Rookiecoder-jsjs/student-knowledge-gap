"""干预闭环路由（intervention-loop-design.md §5）：行动方向 / 干预记录 / 效果验证。

7 端点：action-plan（班/生）、interventions 列表、confirm/skip、effect、summary。
状态机：suggested → done | skipped；done/skipped 是执行事实，终态不可再迁移。
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import _active_kb, _graph, get_db
from app.intervention import (
    action_plan_view,
    intervention_effect,
    intervention_summary,
)
from app.models import Class, Intervention, Student

router = APIRouter()


class InterventionActionRequest(BaseModel):
    """一键确认/跳过请求体（全部可选——一键摩擦下限）。"""

    note: str | None = Field(default=None, max_length=500)


def _student_or_404(db: Session, student_id: int) -> Student:
    stu = db.get(Student, student_id)
    if stu is None:
        raise HTTPException(404, "学生不存在")
    return stu


# ---------------------------------------------------------------------------
# 行动方向（三层结构化数据）
# ---------------------------------------------------------------------------


@router.get("/classes/{class_id}/action-plan")
def class_action_plan(
    class_id: int, exam_id: int | None = None, db: Session = Depends(get_db)
):
    """教学行动方向：全班 → 小组 → 个体三层 + 一键确认用的行 id。"""
    if db.get(Class, class_id) is None:
        raise HTTPException(404, "班级不存在")
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    return action_plan_view(db, graph, class_id, exam_id=exam_id)


@router.get("/students/{student_id}/action-plan")
def student_action_plan(
    student_id: int,
    exam_id: int | None = None,
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    """改进单 get-or-generate（同诊断单模式：有已存直接返回，无则补生成）。"""
    stu = _student_or_404(db, student_id)
    kb = _active_kb(db)
    graph = _graph(db, kb.id)

    from datetime import datetime, time as dtime
    from sqlalchemy import select as _select

    from app.models import Report
    from app.reports.student_action_plan import generate_student_action_plan

    when = (
        datetime.combine(as_of, dtime(23, 59)) if as_of else datetime.now()
    )
    report = db.scalar(
        _select(Report)
        .where(
            Report.type == "student_action_plan",
            Report.student_id == student_id,
            *(  # 指定 exam 时优先该场的存档；未指定取最新一份
                [Report.exam_id == exam_id] if exam_id is not None else []
            ),
        )
        .order_by(Report.generated_at.desc(), Report.id.desc())
        .limit(1)
    )
    if report is None:
        report = generate_student_action_plan(
            db, graph, student_id, as_of=when, exam_id=exam_id
        )
    return {
        "report_id": report.id,
        "markdown": report.content_markdown,
        "as_of": (report.snapshot_json or {}).get("as_of"),
        "writer": (report.snapshot_json or {}).get("writer"),
    }


# ---------------------------------------------------------------------------
# 干预记录列表与状态机
# ---------------------------------------------------------------------------


@router.get("/interventions")
def list_interventions(
    class_id: int | None = None,
    student_id: int | None = None,
    status: str | None = None,
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """干预记录列表（支持按状态过滤；工作台角标用 pending_confirm 计数）。"""
    stmt = select(Intervention).order_by(Intervention.id.desc())
    if class_id is not None:
        stmt = stmt.where(Intervention.class_id == class_id)
    if student_id is not None:
        stmt = stmt.where(Intervention.student_id == student_id)
    if status is not None:
        if status not in ("suggested", "done", "skipped"):
            raise HTTPException(400, f"非法状态过滤值：{status}")
        stmt = stmt.where(Intervention.status == status)
    rows_all = list(db.scalars(stmt))
    page = rows_all[offset : offset + min(limit, 200)]
    graph = None
    if rows_all:
        kb = _active_kb(db)
        graph = _graph(db, kb.id)
    return {"total": len(rows_all), "items": [_row_view(db, r, graph) for r in page]}


def _row_view(db: Session, r: Intervention, graph=None) -> dict:
    kp = graph.kp(r.kp_id) if graph is not None else None
    alias = None
    if r.student_id is not None:
        stu = db.get(Student, r.student_id)
        alias = stu.name_or_alias if stu else None
    return {
        "id": r.id,
        "class_id": r.class_id,
        "student_id": r.student_id,
        "alias": alias,
        "kp_code": getattr(kp, "code", None),
        "kp_name": getattr(kp, "name", None),
        "kind": r.kind,
        "scope": r.scope,
        "group_ref": r.group_ref,
        "status": r.status,
        "note": r.note,
        "baseline_as_of": str(r.baseline_as_of.date()),
        "suggested_at": r.suggested_at.isoformat() if r.suggested_at else None,
        "done_at": r.done_at.isoformat() if r.done_at else None,
    }


@router.post("/interventions/{intervention_id}/confirm")
def confirm_intervention(
    intervention_id: int, req: InterventionActionRequest | None = None, db: Session = Depends(get_db)
):
    """一键确认执行（body 可选 note；默认当前时间戳 done_at）。"""
    iv = db.get(Intervention, intervention_id)
    if iv is None:
        raise HTTPException(404, "干预记录不存在")
    if iv.status != "suggested":
        raise HTTPException(400, f"干预记录状态为 {iv.status}，不能再确认")
    iv.status = "done"
    iv.done_at = datetime.now()
    note = (req.note if req else None) or None
    if note:
        iv.note = note
    db.commit()
    return {"id": iv.id, "status": iv.status, "done_at": iv.done_at.isoformat()}


@router.post("/interventions/{intervention_id}/skip")
def skip_intervention(
    intervention_id: int, req: InterventionActionRequest | None = None, db: Session = Depends(get_db)
):
    """跳过（可选 note；skip 也是信号，不强制理由）。"""
    iv = db.get(Intervention, intervention_id)
    if iv is None:
        raise HTTPException(404, "干预记录不存在")
    if iv.status != "suggested":
        raise HTTPException(400, f"干预记录状态为 {iv.status}，不能再跳过")
    iv.status = "skipped"
    note = (req.note if req else None) or None
    if note:
        iv.note = note
    db.commit()
    return {"id": iv.id, "status": iv.status}


# ---------------------------------------------------------------------------
# 效果验证与闭环度量
# ---------------------------------------------------------------------------


@router.get("/interventions/{intervention_id}/effect")
def single_effect(intervention_id: int, db: Session = Depends(get_db)):
    """单条效果推导（derive-on-read；awaiting_retest 为试点期常态）。"""
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    try:
        return intervention_effect(db, graph, intervention_id)
    except LookupError as e:
        raise HTTPException(404, str(e))


@router.get("/interventions/summary")
def interventions_summary(class_id: int, db: Session = Depends(get_db)):
    """闭环度量：采纳率 + 干预提升率（北极星；分母只算可评估子集）。"""
    if db.get(Class, class_id) is None:
        raise HTTPException(404, "班级不存在")
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    return intervention_summary(db, graph, class_id)
