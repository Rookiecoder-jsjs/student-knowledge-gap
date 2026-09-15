"""学生自服务面（auth-roles-design §6）：/me 命名空间 + 管理员预览镜像。

不复用 /students/{id}（防 id 探测）——学生主体只能读自己的掌握/薄弱/已签发报告。
全部**只读、只发 issued 内容**；学生永不触发分析/签发/写操作（draft 不可见）。
教师视角走原 /students/{id}/... 端点不变。

**刻意例外（study-loop-design）**：学习方案的 get-or-generate（``/me/study-plan``）
与学生自报（``/me/study-records/{id}/self-mark``）是学生自服务的写端点——修复段
责任重分配后学生是学习执行主体。仅限 self 端点可写；预览镜像仍严格只读
（不触发生成、无自报按钮的数据面）。

管理员预览（frontend-ends-design 超级账号）：``/admin/students/{id}/portal/*`` 与
/me 共用同一组 payload 函数——只读、只发 issued 的语义由构造保证一致，只是主体从
「登录学生本人」换成「admin 指定的学生 + 班级归属校验」。**刻意不复用教师端端点
代餐**：``/students/{id}/action-plan`` 是 get-or-generate（会写库），
``/reports?student_id=`` 含 draft——两者都偏离学生视角。
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    _active_kb,
    _as_dt,
    _graph,
    get_db,
    guard_class,
    require_admin,
    require_student,
)
from app.inbox import TYPE_LABELS
from app.models import Report, StudyRecord, Student
from app.pipeline.mastery import mastery_at
from app.pipeline.progress import loop_states_for_student
from app.pipeline.weakness import assess_student_kps
from app.queries.knowledge_graph import student_knowledge_graph
from app.study import get_or_generate_study_record, self_mark_learned, self_report_map

router = APIRouter()
MAX_PORTAL_PAGE = 200


# ---------------------------------------------------------------------------
# payload 构造：/me 与学生门户预览的唯一出处（形状不复制）
# ---------------------------------------------------------------------------


def _profile_payload(s: Student) -> dict:
    return {
        "student": {
            "id": s.id,
            "name_or_alias": s.name_or_alias,
            "external_code": s.external_code,
            "class_id": s.class_id,
            "class_name": s.clazz.name if s.clazz is not None else None,
            "school_id": s.school_id,
        }
    }


def _mastery_payload(db: Session, s: Student, when: datetime) -> dict:
    """掌握度（与学生诊断单/教师端同算法，仅限该生）。"""
    kb = _active_kb(db, s.clazz.subject if s.clazz else None)
    graph = _graph(db, kb.id)
    out = []
    for kp_id in graph.grade7_kp_ids():
        kp = graph.kp(kp_id)
        m = mastery_at(db, s.id, kp_id, when)
        if m is not None:
            out.append({"code": kp.code, "name": kp.name, "mastery": round(m, 3)})
    return {"student_id": s.id, "as_of": str(when.date()), "mastery": out}


def _knowledge_graph_payload(db: Session, s: Student, when: datetime) -> dict:
    """学生图谱只读面：结构与 active KB 对齐，指标仅含该生。"""
    kb = _active_kb(db, s.clazz.subject if s.clazz else None)
    graph = _graph(db, kb.id)
    return student_knowledge_graph(db, graph, s.id, s.class_id, when)


def _weaknesses_payload(db: Session, s: Student, when: datetime) -> dict:
    """薄弱点（与教师端 /students/{id}/weaknesses 同形状）。"""
    kb = _active_kb(db, s.clazz.subject if s.clazz else None)
    graph = _graph(db, kb.id)
    assessments = assess_student_kps(db, graph, s.id, s.class_id, when)
    # 进度生命周期（闭环一期 P1）：薄弱项携带干预进度状态，门户呈现闭环故事
    loop = loop_states_for_student(db, graph, s.id, s.class_id, when, assessments=assessments)
    return {
        "student_id": s.id,
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
                "loop_state": loop.get(a.kp_id),
            }
            for a in assessments
            if a.is_weak
        ],
        "gates": {
            "未学到": sum(1 for a in assessments if a.gate == "未学到"),
            "数据不足": sum(1 for a in assessments if a.gate == "数据不足"),
        },
    }


def _validate_page(offset: int, limit: int) -> None:
    if offset < 0:
        raise ValueError("offset 不能小于 0")
    if not 1 <= limit <= MAX_PORTAL_PAGE:
        raise ValueError(f"limit 必须在 1 到 {MAX_PORTAL_PAGE} 之间")


def _reports_payload(
    db: Session, s: Student, *, offset: int = 0, limit: int = 50
) -> dict:
    """已签发报告列表（draft/archived 不可见）。"""
    _validate_page(offset, limit)
    filters = [Report.student_id == s.id, Report.status == "issued"]
    total = db.scalar(select(func.count(Report.id)).where(*filters)) or 0
    rows = list(
        db.scalars(
            select(Report)
            .where(*filters)
            .order_by(Report.generated_at.desc(), Report.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return {
        "reports": [
            {
                "report_id": r.id,
                "type": r.type,
                "type_label": TYPE_LABELS.get(r.type, r.type),
                "class_id": r.class_id,
                "exam_id": r.exam_id,
                "generated_at": r.generated_at.isoformat(timespec="seconds")
                if r.generated_at
                else None,
            }
            for r in rows
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
    }


def _issued_own(db: Session, report_id: int, student_id: int) -> Report:
    r = db.scalar(
        select(Report).where(
            Report.id == report_id,
            Report.student_id == student_id,
            Report.status == "issued",
        )
    )
    if r is None:
        raise HTTPException(404, "报告不存在或未签发")
    return r


def _report_full_payload(db: Session, s: Student, report_id: int) -> dict:
    r = _issued_own(db, report_id, s.id)
    return {
        "report_id": r.id,
        "type": r.type,
        "type_label": TYPE_LABELS.get(r.type, r.type),
        "class_id": r.class_id,
        "exam_id": r.exam_id,
        "generated_at": r.generated_at.isoformat(timespec="seconds")
        if r.generated_at
        else None,
        "markdown": r.content_markdown,
        "snapshot": r.snapshot_json,
    }


def _action_plan_payload(db: Session, s: Student) -> dict:
    """最新已签发改进单（只读已签发；不触发 get-or-generate）。"""
    r = db.scalar(
        select(Report)
        .where(
            Report.type == "student_action_plan",
            Report.student_id == s.id,
            Report.status == "issued",
        )
        .order_by(Report.generated_at.desc(), Report.id.desc())
        .limit(1)
    )
    if r is None:
        return {"report_id": None, "markdown": None, "as_of": None}
    return {
        "report_id": r.id,
        "markdown": r.content_markdown,
        "as_of": (r.snapshot_json or {}).get("as_of"),
    }


# ---------------------------------------------------------------------------
# 学习方案 + 自报（study-loop-design）：/me 唯一可写面（预览镜像严格只读）
# ---------------------------------------------------------------------------


def _study_list_payload(
    db: Session,
    s: Student,
    when: datetime,
    *,
    offset: int = 0,
    limit: int = 50,
) -> dict:
    """学习记录列表（不含方案正文；行级带折叠态，弱项卡片/学习页共用）。"""
    _validate_page(offset, limit)
    kb = _active_kb(db, s.clazz.subject if s.clazz else None)
    graph = _graph(db, kb.id)
    total = db.scalar(
        select(func.count(StudyRecord.id)).where(StudyRecord.student_id == s.id)
    ) or 0
    recs = list(
        db.scalars(
            select(StudyRecord)
            .where(StudyRecord.student_id == s.id)
            .order_by(StudyRecord.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    loop = loop_states_for_student(
        db, graph, s.id, s.class_id, when,
        self_reports=self_report_map(db, [s.id]),
    )
    return {
        "student_id": s.id,
        "records": [
            {
                "id": r.id,
                "kp_code": graph.kp(r.kp_id).code,
                "kp_name": graph.kp(r.kp_id).name,
                "generated_at": r.generated_at.isoformat(timespec="seconds"),
                "self_marked_at": r.self_marked_at.isoformat(timespec="seconds")
                if r.self_marked_at
                else None,
                "loop_state": loop.get(r.kp_id),
            }
            for r in recs
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
    }


def _study_plan_payload(
    db: Session, s: Student, kp_code: str, when: datetime, *, allow_generate: bool
) -> dict:
    """学习方案视图。self 走 get-or-generate（可写）；预览 allow_generate=False
    （无记录 404，绝不触发 LLM/写库）。"""
    kb = _active_kb(db, s.clazz.subject if s.clazz else None)
    graph = _graph(db, kb.id)
    kp_id = next(
        (kid for kid in graph.grade7_kp_ids() if graph.kp(kid).code == kp_code), None
    )
    if kp_id is None:
        raise HTTPException(404, "知识点不存在")
    try:
        rec = get_or_generate_study_record(
            db, graph, s, kp_id, allow_generate=allow_generate, now=when
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    loop = loop_states_for_student(
        db, graph, s.id, s.class_id, when,
        self_reports=self_report_map(db, [s.id]),
    )
    kp = graph.kp(kp_id)
    return {
        "id": rec.id,
        "kp_code": kp.code,
        "kp_name": kp.name,
        "plan_markdown": rec.plan_markdown,
        "plan_writer": rec.plan_writer,
        "generated_at": rec.generated_at.isoformat(timespec="seconds"),
        "self_marked_at": rec.self_marked_at.isoformat(timespec="seconds")
        if rec.self_marked_at
        else None,
        "loop_state": loop.get(kp_id),
    }


# ---------------------------------------------------------------------------
# /me：学生本人
# ---------------------------------------------------------------------------


def _self(ctx) -> Student:
    """学生主体（require_student 已保证非空）。"""
    return ctx.student


@router.get("/me")
def me_profile(ctx=Depends(require_student), db: Session = Depends(get_db)):
    return _profile_payload(_self(ctx))


@router.get("/me/mastery")
def me_mastery(
    as_of: date | None = None,
    ctx=Depends(require_student),
    db: Session = Depends(get_db),
):
    return _mastery_payload(db, _self(ctx), _as_dt(as_of))


@router.get("/me/knowledge-graph")
def me_knowledge_graph(
    as_of: date | None = None,
    ctx=Depends(require_student),
    db: Session = Depends(get_db),
):
    return _knowledge_graph_payload(db, _self(ctx), _as_dt(as_of))


@router.get("/me/weaknesses")
def me_weaknesses(
    as_of: date | None = None,
    ctx=Depends(require_student),
    db: Session = Depends(get_db),
):
    return _weaknesses_payload(db, _self(ctx), _as_dt(as_of))


@router.get("/me/reports")
def me_reports(
    offset: int = 0,
    limit: int = 50,
    ctx=Depends(require_student),
    db: Session = Depends(get_db),
):
    try:
        return _reports_payload(db, _self(ctx), offset=offset, limit=limit)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/me/reports/{report_id}/full")
def me_report_full(
    report_id: int,
    ctx=Depends(require_student),
    db: Session = Depends(get_db),
):
    return _report_full_payload(db, _self(ctx), report_id)


@router.get("/me/action-plan")
def me_action_plan(ctx=Depends(require_student), db: Session = Depends(get_db)):
    return _action_plan_payload(db, _self(ctx))


@router.get("/me/study-records")
def me_study_records(
    offset: int = 0,
    limit: int = 50,
    ctx=Depends(require_student),
    db: Session = Depends(get_db),
):
    try:
        return _study_list_payload(
            db, _self(ctx), datetime.now(), offset=offset, limit=limit
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/me/study-plan")
def me_study_plan(
    kp_code: str,
    as_of: date | None = None,
    ctx=Depends(require_student),
    db: Session = Depends(get_db),
):
    """学习方案（get-or-generate：首次查看生成并缓存；幂等复用不重调 LLM）。

    无显式 as_of 用真实时刻——方案生成/轮次锚点要真时间；显式 as_of 走
    当日 23:59 约定（历史查看口径，与 weaknesses 一致）。
    """
    when = _as_dt(as_of) if as_of is not None else datetime.now()
    return _study_plan_payload(db, _self(ctx), kp_code, when, allow_generate=True)


@router.post("/me/study-records/{record_id}/self-mark")
def me_self_mark(
    record_id: int,
    ctx=Depends(require_student),
    db: Session = Depends(get_db),
):
    """自报「我学会了」：软闭合——只推进干预状态机，掌握度不动（app.study 硬边界）。"""
    s = _self(ctx)
    kb = _active_kb(db, s.clazz.subject if s.clazz else None)
    graph = _graph(db, kb.id)
    try:
        out = self_mark_learned(db, graph, s, record_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    db.commit()
    return out


# ---------------------------------------------------------------------------
# 学生门户预览（frontend-ends-design 超级账号）：admin 专用只读镜像
# ---------------------------------------------------------------------------


def _preview_student(
    student_id: int,
    ctx=Depends(require_admin),
    db: Session = Depends(get_db),
) -> Student:
    """预览主体：admin 身份 + 班级归属校验（admin 全班可见）。"""
    s = db.get(Student, student_id)
    if s is None:
        raise HTTPException(404, "学生不存在")
    guard_class(s.class_id, db, ctx)
    return s


@router.get("/admin/students/{student_id}/portal")
def preview_profile(s: Student = Depends(_preview_student)):
    return _profile_payload(s)


@router.get("/admin/students/{student_id}/portal/mastery")
def preview_mastery(
    as_of: date | None = None,
    s: Student = Depends(_preview_student),
    db: Session = Depends(get_db),
):
    return _mastery_payload(db, s, _as_dt(as_of))


@router.get("/admin/students/{student_id}/portal/knowledge-graph")
def preview_knowledge_graph(
    as_of: date | None = None,
    s: Student = Depends(_preview_student),
    db: Session = Depends(get_db),
):
    return _knowledge_graph_payload(db, s, _as_dt(as_of))


@router.get("/admin/students/{student_id}/portal/weaknesses")
def preview_weaknesses(
    as_of: date | None = None,
    s: Student = Depends(_preview_student),
    db: Session = Depends(get_db),
):
    return _weaknesses_payload(db, s, _as_dt(as_of))


@router.get("/admin/students/{student_id}/portal/reports")
def preview_reports(
    offset: int = 0,
    limit: int = 50,
    s: Student = Depends(_preview_student),
    db: Session = Depends(get_db),
):
    try:
        return _reports_payload(db, s, offset=offset, limit=limit)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/admin/students/{student_id}/portal/reports/{report_id}/full")
def preview_report_full(
    report_id: int,
    s: Student = Depends(_preview_student),
    db: Session = Depends(get_db),
):
    return _report_full_payload(db, s, report_id)


@router.get("/admin/students/{student_id}/portal/action-plan")
def preview_action_plan(
    s: Student = Depends(_preview_student),
    db: Session = Depends(get_db),
):
    return _action_plan_payload(db, s)


@router.get("/admin/students/{student_id}/portal/study-records")
def preview_study_records(
    offset: int = 0,
    limit: int = 50,
    s: Student = Depends(_preview_student),
    db: Session = Depends(get_db),
):
    try:
        return _study_list_payload(
            db, s, datetime.now(), offset=offset, limit=limit
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/admin/students/{student_id}/portal/study-plan")
def preview_study_plan(
    kp_code: str,
    s: Student = Depends(_preview_student),
    db: Session = Depends(get_db),
):
    """预览学习方案：严格只读（allow_generate=False），无记录 404，不触发 LLM。"""
    return _study_plan_payload(
        db, s, kp_code, _as_dt(None), allow_generate=False
    )
