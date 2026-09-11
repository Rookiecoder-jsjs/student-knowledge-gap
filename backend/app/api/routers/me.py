"""学生自服务只读面（auth-roles-design §6）：/me 命名空间 + 管理员预览镜像。

不复用 /students/{id}（防 id 探测）——学生主体只能读自己的掌握/薄弱/已签发报告。
全部**只读、只发 issued 内容**；学生永不触发分析/签发/写操作（draft 不可见）。
教师视角走原 /students/{id}/... 端点不变。

管理员预览（frontend-ends-design 超级账号）：``/admin/students/{id}/portal/*`` 与
/me 共用同一组 payload 函数——只读、只发 issued 的语义由构造保证一致，只是主体从
「登录学生本人」换成「admin 指定的学生 + 班级归属校验」。**刻意不复用教师端端点
代餐**：``/students/{id}/action-plan`` 是 get-or-generate（会写库），
``/reports?student_id=`` 含 draft——两者都偏离学生视角。
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
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
from app.models import Report, Student
from app.pipeline.mastery import mastery_at
from app.pipeline.weakness import assess_student_kps

router = APIRouter()


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
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    out = []
    for kp_id in graph.grade7_kp_ids():
        kp = graph.kp(kp_id)
        m = mastery_at(db, s.id, kp_id, when)
        if m is not None:
            out.append({"code": kp.code, "name": kp.name, "mastery": round(m, 3)})
    return {"student_id": s.id, "as_of": str(when.date()), "mastery": out}


def _weaknesses_payload(db: Session, s: Student, when: datetime) -> dict:
    """薄弱点（与教师端 /students/{id}/weaknesses 同形状）。"""
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    assessments = assess_student_kps(db, graph, s.id, s.class_id, when)
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
            }
            for a in assessments
            if a.is_weak
        ],
        "gates": {
            "未学到": sum(1 for a in assessments if a.gate == "未学到"),
            "数据不足": sum(1 for a in assessments if a.gate == "数据不足"),
        },
    }


def _reports_payload(db: Session, s: Student) -> dict:
    """已签发报告列表（draft/archived 不可见）。"""
    rows = list(
        db.scalars(
            select(Report)
            .where(Report.student_id == s.id, Report.status == "issued")
            .order_by(Report.generated_at.desc(), Report.id.desc())
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
        ]
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


@router.get("/me/weaknesses")
def me_weaknesses(
    as_of: date | None = None,
    ctx=Depends(require_student),
    db: Session = Depends(get_db),
):
    return _weaknesses_payload(db, _self(ctx), _as_dt(as_of))


@router.get("/me/reports")
def me_reports(ctx=Depends(require_student), db: Session = Depends(get_db)):
    return _reports_payload(db, _self(ctx))


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


@router.get("/admin/students/{student_id}/portal/weaknesses")
def preview_weaknesses(
    as_of: date | None = None,
    s: Student = Depends(_preview_student),
    db: Session = Depends(get_db),
):
    return _weaknesses_payload(db, s, _as_dt(as_of))


@router.get("/admin/students/{student_id}/portal/reports")
def preview_reports(s: Student = Depends(_preview_student), db: Session = Depends(get_db)):
    return _reports_payload(db, s)


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
