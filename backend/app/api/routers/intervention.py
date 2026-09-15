"""干预闭环路由（intervention-loop-design.md §5）：行动方向 / 干预记录 / 效果验证。

端点：action-plan（班/生）、interventions 列表、confirm/skip（with_group 批量）、
effect、summary。状态机：suggested → done | skipped；done/skipped 是执行事实，
终态不可再迁移。

闭环一期（progress-loop-design）增补：行级进度状态 loop_state（折叠函数派生）。
定向复测（retest-blueprint / link-retest）已于 2026-09-11 软退役（study-loop-design：
验证语义由软闭合+自然考试被动验证接管，教师出卷路径无人使用）——retest_exam_id
列与历史关联保留（只停新写入），「诊断」考试类型与管线照常支持手动建卷。
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth as _auth
from app.api.deps import _active_kb, _graph, get_db, guard_class, require_teacher
from app.intervention import (
    SCOPE_GROUP,
    action_plan_view,
    intervention_effect,
    intervention_summary,
)
from app.models import Class, Intervention, KnowledgePoint, Student
from app.pipeline.progress import loop_states_for_student, row_loop_state

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
    class_id: int,
    exam_id: int | None = None,
    ctx=Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """教学行动方向：全班 → 小组 → 个体三层 + 一键确认用的行 id。"""
    clazz = db.get(Class, class_id)
    if clazz is None:
        raise HTTPException(404, "班级不存在")
    guard_class(class_id, db, ctx)
    kb = _active_kb(db, _auth.class_subject(db, ctx, clazz))
    graph = _graph(db, kb.id)
    return action_plan_view(db, graph, class_id, exam_id=exam_id)


@router.get("/students/{student_id}/action-plan")
def student_action_plan(
    student_id: int,
    exam_id: int | None = None,
    as_of: date | None = None,
    ctx=Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """改进单 get-or-generate（同诊断单模式：有已存直接返回，无则补生成）。"""
    stu = _student_or_404(db, student_id)
    guard_class(stu.class_id, db, ctx)
    kb = _active_kb(
        db, _auth.class_subject(db, ctx, stu.clazz) if stu.clazz else None
    )
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
    ctx=Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """干预记录列表（支持按状态过滤；工作台角标用 pending_confirm 计数）。

    显式传 class_id 时做归属校验；未传时安全模式下收敛到授权班级集合。
    """
    from app.api.deps import _auth as auth_mod

    if class_id is not None:
        if db.get(Class, class_id) is None:
            raise HTTPException(404, "班级不存在")
        guard_class(class_id, db, ctx)
    if student_id is not None:
        stu = db.get(Student, student_id)
        if stu is None:
            raise HTTPException(404, "学生不存在")
        guard_class(stu.class_id, db, ctx)
    if offset < 0:
        raise HTTPException(400, "offset 不能小于 0")
    if not 1 <= limit <= 200:
        raise HTTPException(400, "limit 必须在 1 到 200 之间")

    conds = []
    if class_id is not None:
        conds.append(Intervention.class_id == class_id)
    else:
        allowed = auth_mod.allowed_class_ids(db, ctx)
        if allowed is not None:
            conds.append(Intervention.class_id.in_(allowed or [-1]))
    if student_id is not None:
        conds.append(Intervention.student_id == student_id)
    if status is not None:
        if status not in ("suggested", "done", "skipped"):
            raise HTTPException(400, f"非法状态过滤值：{status}")
        conds.append(Intervention.status == status)
    total = db.scalar(select(func.count(Intervention.id)).where(*conds)) or 0
    stmt = select(Intervention).where(*conds).order_by(Intervention.id.desc())
    page = list(db.scalars(stmt.offset(offset).limit(limit)))
    loop_cache: dict[int, dict[int, str]] = {}
    items = []
    for r in page:
        loop = (
            _student_loop(db, ctx, r.student_id, loop_cache)
            if r.student_id is not None
            else None
        )
        items.append(_row_view(db, r, loop=loop))
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(items) < total,
        "items": items,
    }


@router.get("/interventions/student-summary")
def intervention_student_summary(
    class_id: int, ctx=Depends(require_teacher), db: Session = Depends(get_db)
):
    """返回班级内按学生聚合的干预执行计数。

    学生名单页只需要角标计数，不应为每一页学生拉取全部干预并逐人重算
    loop_state。这里使用单条分组查询，保留事实层状态，避免把重型闭环推导
    放进列表首屏路径。
    """
    clazz = db.get(Class, class_id)
    if clazz is None:
        raise HTTPException(404, "班级不存在")
    guard_class(class_id, db, ctx)
    rows = db.execute(
        select(Intervention.student_id, Intervention.status, func.count(Intervention.id))
        .where(
            Intervention.class_id == class_id,
            Intervention.student_id.is_not(None),
        )
        .group_by(Intervention.student_id, Intervention.status)
    )
    by_student: dict[int, dict[str, int]] = {}
    for student_id, status, count in rows:
        if student_id is None:
            continue
        item = by_student.setdefault(student_id, {"suggested": 0, "done": 0})
        if status in item:
            item[status] = int(count)
    return {
        "class_id": class_id,
        "students": [
            {"student_id": student_id, **counts}
            for student_id, counts in by_student.items()
        ],
    }


def _student_loop(
    db: Session, ctx, student_id: int, cache: dict[int, dict[int, str]]
) -> dict[int, str]:
    """按学生缓存的进度折叠（列表页同一学生多行只算一次）。"""
    if student_id in cache:
        return cache[student_id]
    stu = db.get(Student, student_id)
    if stu is None:
        cache[student_id] = {}
        return cache[student_id]
    kb = _active_kb(
        db, _auth.class_subject(db, ctx, stu.clazz) if stu.clazz else None
    )
    graph = _graph(db, kb.id)
    cache[student_id] = loop_states_for_student(
        db, graph, student_id, stu.class_id, datetime.now()
    )
    return cache[student_id]


def _row_view(
    db: Session, r: Intervention, graph=None, loop: dict[int, str] | None = None
) -> dict:
    # 干预行可能来自多个学科/历史版本；直接按 kp_id 回查，避免用第一页记录
    # 的 graph 解释整页数据，导致混学科时错误名称或 KeyError。
    kp = db.get(KnowledgePoint, r.kp_id)
    if kp is None and graph is not None:
        try:
            kp = graph.kp(r.kp_id)
        except KeyError:
            kp = None
    alias = None
    if r.student_id is not None:
        stu = db.get(Student, r.student_id)
        alias = stu.name_or_alias if stu else None
    # 行级进度状态（闭环一期 P1）：学生行走折叠函数（个体判决），
    # 班级/小组集体行只给三态、不下个体判决
    if r.student_id is not None and loop is not None:
        state = loop.get(r.kp_id)
    else:
        state = row_loop_state(r)
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
        "loop_state": state,
        "retest_exam_id": r.retest_exam_id,
        "note": r.note,
        "baseline_as_of": str(r.baseline_as_of.date()),
        "suggested_at": r.suggested_at.isoformat() if r.suggested_at else None,
        "done_at": r.done_at.isoformat() if r.done_at else None,
    }


@router.post("/interventions/{intervention_id}/confirm")
def confirm_intervention(
    intervention_id: int,
    req: InterventionActionRequest | None = None,
    with_group: bool = False,
    ctx=Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """一键确认执行（body 可选 note；默认当前时间戳 done_at）。

    with_group=true 且目标行是小组行：同 group_ref 的全部挂起行批量落
    「已执行」——队列按组折叠成一行展示，操作层一次、事实层逐行。
    """
    iv = db.get(Intervention, intervention_id)
    if iv is None:
        raise HTTPException(404, "干预记录不存在")
    guard_class(iv.class_id, db, ctx)
    if iv.status != "suggested":
        raise HTTPException(400, f"干预记录状态为 {iv.status}，不能再确认")
    targets = [iv]
    if with_group and iv.scope == SCOPE_GROUP and iv.group_ref:
        targets = list(
            db.scalars(
                select(Intervention).where(
                    Intervention.class_id == iv.class_id,
                    Intervention.group_ref == iv.group_ref,
                    Intervention.status == "suggested",
                )
            )
        )
    now = datetime.now()
    note = (req.note if req else None) or None
    for t in targets:
        t.status = "done"
        t.done_at = now
        if note:
            t.note = note
    db.commit()
    return {
        "id": iv.id,
        "status": iv.status,
        "done_at": now.isoformat(),
        "confirmed": len(targets),
    }


@router.post("/interventions/{intervention_id}/skip")
def skip_intervention(
    intervention_id: int,
    req: InterventionActionRequest | None = None,
    with_group: bool = False,
    ctx=Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """跳过（可选 note；skip 也是信号，不强制理由）。with_group 同 confirm。"""
    iv = db.get(Intervention, intervention_id)
    if iv is None:
        raise HTTPException(404, "干预记录不存在")
    guard_class(iv.class_id, db, ctx)
    if iv.status != "suggested":
        raise HTTPException(400, f"干预记录状态为 {iv.status}，不能再跳过")
    targets = [iv]
    if with_group and iv.scope == SCOPE_GROUP and iv.group_ref:
        targets = list(
            db.scalars(
                select(Intervention).where(
                    Intervention.class_id == iv.class_id,
                    Intervention.group_ref == iv.group_ref,
                    Intervention.status == "suggested",
                )
            )
        )
    note = (req.note if req else None) or None
    for t in targets:
        t.status = "skipped"
        if note:
            t.note = note
    db.commit()
    return {"id": iv.id, "status": iv.status, "skipped": len(targets)}


# ---------------------------------------------------------------------------
# 效果验证与闭环度量
# ---------------------------------------------------------------------------


@router.get("/interventions/{intervention_id}/effect")
def single_effect(
    intervention_id: int, ctx=Depends(require_teacher), db: Session = Depends(get_db)
):
    """单条效果推导（derive-on-read；awaiting_retest 为试点期常态）。"""
    iv = db.get(Intervention, intervention_id)
    if iv is not None:
        guard_class(iv.class_id, db, ctx)
    _iv_cls = db.get(Class, iv.class_id) if iv is not None else None
    kb = _active_kb(
        db,
        _auth.class_subject(db, ctx, _iv_cls) if _iv_cls is not None else None,
    )
    graph = _graph(db, kb.id)
    try:
        return intervention_effect(db, graph, intervention_id)
    except LookupError as e:
        raise HTTPException(404, str(e))


@router.get("/interventions/summary")
def interventions_summary(
    class_id: int, ctx=Depends(require_teacher), db: Session = Depends(get_db)
):
    """闭环度量：采纳率 + 干预提升率（北极星；分母只算可评估子集）。"""
    clazz = db.get(Class, class_id)
    if clazz is None:
        raise HTTPException(404, "班级不存在")
    guard_class(class_id, db, ctx)
    kb = _active_kb(db, _auth.class_subject(db, ctx, clazz))
    graph = _graph(db, kb.id)
    return intervention_summary(db, graph, class_id)
