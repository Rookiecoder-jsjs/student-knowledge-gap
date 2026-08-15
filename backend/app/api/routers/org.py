"""组织域路由：学校 / 班级 / 学生 / 教学进度 / 考试列表与详情（候选2 拆分）。

列表聚合在 ``queries/``（N+1 → 批量取）；本文件只做依赖解析与异常翻译。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import _active_kb, _graph, get_db
from app.kb.resolver import KbNotActiveError, active_kb
from app.models import Class, School, Student, TeachingProgress
from app.queries import classes as query_classes
from app.queries import exams as query_exams
from app.queries.classes_overview import classes_overview as query_classes_overview
from app.schemas import ClassCreate, ProgressPatchRequest, ProgressUpdate, SchoolCreate

router = APIRouter()


# ---------------------------------------------------------------------------
# 学校 / 班级
# ---------------------------------------------------------------------------


@router.post("/schools")
def create_school(req: SchoolCreate, db: Session = Depends(get_db)):
    school = School(name=req.name)
    db.add(school)
    db.flush()
    return {"school_id": school.id}


@router.post("/schools/{school_id}/classes")
def create_class(school_id: int, req: ClassCreate, db: Session = Depends(get_db)):
    if db.get(School, school_id) is None:
        raise HTTPException(404, "学校不存在")
    clazz = Class(school_id=school_id, name=req.name, grade=req.grade, subject=req.subject)
    db.add(clazz)
    db.flush()
    student_ids = []
    for alias in req.student_aliases:
        stu = Student(
            school_id=school_id, class_id=clazz.id, name_or_alias=alias
        )
        db.add(stu)
        db.flush()
        student_ids.append(stu.id)
    return {"class_id": clazz.id, "student_ids": student_ids}


@router.post("/classes/{class_id}/progress")
def update_progress(class_id: int, req: ProgressUpdate, db: Session = Depends(get_db)):
    kb = _active_kb(db)
    graph = _graph(db, kb.id)

    added = 0
    for code in req.kp_codes:
        try:
            kp_id = graph.code(code)
        except KeyError:
            raise HTTPException(400, f"知识点编码不存在: {code}")
        if getattr(graph.kp(kp_id), "archived", False):
            raise HTTPException(400, f"知识点 {code} 已归档，不可标记教学进度")
        exists = db.scalar(
            select(TeachingProgress.id).where(
                TeachingProgress.class_id == class_id,
                TeachingProgress.kp_id == kp_id,
            )
        )
        if exists is None:
            db.add(
                TeachingProgress(class_id=class_id, kp_id=kp_id, taught_at=req.taught_at)
            )
            added += 1
    return {"added": added}


# ---------------------------------------------------------------------------
# 列表与回读（前端导航依赖）
# ---------------------------------------------------------------------------


@router.get("/classes")
def list_classes(db: Session = Depends(get_db)):
    """聚合在 queries.classes（每班 2 次 count → 2 次全量 group_by）。"""
    return {"classes": query_classes.classes_list(db)}


@router.get("/classes/overview")
def classes_overview(db: Session = Depends(get_db)):
    """所有班级的轻量概览（一级「班级概览」页用，一次返回避免前端 N+1）。

    每班汇总：待办考试数、最近一场考试状态、教学进度覆盖（与分析层同分母）。
    active kb 缺失时 progress 返回 {0,0}，不抛错——一级页面不能因未导入知识库整页 500。
    聚合逻辑在 ``queries/classes_overview``（候选2 深模块）；本端点只做 kb 解析与兜底。
    """
    # 领域层信号（kb.resolver，问题6）：active_kb 无版本返回 None、strict 无 active 抛
    # KbNotActiveError，均按「无知识库」兜底，不把 HTTP 异常当控制流。
    try:
        kb = active_kb(db)
    except KbNotActiveError:
        kb = None
    grade7_set = set(_graph(db, kb.id).grade7_kp_ids()) if kb is not None else set()
    return query_classes_overview(db, grade7_set)


@router.get("/classes/{class_id}/students")
def list_students(class_id: int, db: Session = Depends(get_db)):
    if db.get(Class, class_id) is None:
        raise HTTPException(404, "班级不存在")
    students = db.scalars(
        select(Student).where(Student.class_id == class_id).order_by(Student.id)
    )
    # 名单原序返回；禁止按分数排序由前端约束，此处不提供任何分数字段
    return {
        "class_id": class_id,
        "students": [
            {
                "student_id": s.id,
                "name_or_alias": s.name_or_alias,
                "external_code": s.external_code,
            }
            for s in students
        ],
    }


@router.get("/classes/{class_id}/progress")
def get_progress(class_id: int, db: Session = Depends(get_db)):
    if db.get(Class, class_id) is None:
        raise HTTPException(404, "班级不存在")
    return {"class_id": class_id, "progress": query_classes.progress_list(db, class_id)}


@router.delete("/classes/{class_id}/progress/{kp_id}")
def delete_progress(class_id: int, kp_id: int, db: Session = Depends(get_db)):
    """取消已教标记（kb-edit §4.2）。既有标记可删（含 archived kp 的残留）。"""
    row = db.scalar(
        select(TeachingProgress).where(
            TeachingProgress.class_id == class_id, TeachingProgress.kp_id == kp_id
        )
    )
    if row is None:
        raise HTTPException(404, "未找到该教学进度记录")
    db.delete(row)
    db.flush()
    return {"deleted_kp_id": kp_id}


@router.patch("/classes/{class_id}/progress/{kp_id}")
def patch_progress(
    class_id: int,
    kp_id: int,
    req: ProgressPatchRequest,
    db: Session = Depends(get_db),
):
    """改 taught_at 日期（kb-edit §4.2）。"""
    row = db.scalar(
        select(TeachingProgress).where(
            TeachingProgress.class_id == class_id, TeachingProgress.kp_id == kp_id
        )
    )
    if row is None:
        raise HTTPException(404, "未找到该教学进度记录")
    row.taught_at = req.taught_at
    db.flush()
    return {"class_id": class_id, "kp_id": kp_id, "taught_at": str(row.taught_at)}


# ---------------------------------------------------------------------------
# 考试列表与回读
# ---------------------------------------------------------------------------


@router.get("/exams")
def list_exams(class_id: int | None = None, db: Session = Depends(get_db)):
    """聚合在 queries.exams（状态/未审标注/题数各一次 group_by，替代逐场 N+1）。"""
    return {"exams": query_exams.exams_list(db, class_id)}


@router.get("/exams/{exam_id}")
def exam_detail(exam_id: int, db: Session = Depends(get_db)):
    """详情聚合在 queries.exams（逐题逐标签回查 → 一次 in_ 取）。"""
    data = query_exams.exam_detail(db, exam_id)
    if data is None:
        raise HTTPException(404, "考试不存在")
    return data


@router.get("/exams/{exam_id}/responses")
def exam_responses(exam_id: int, db: Session = Depends(get_db)):
    """学生×作答状态矩阵（名单原序；低置信题计数一次聚合供审核台角标）。"""
    data = query_exams.exam_responses(db, exam_id)
    if data is None:
        raise HTTPException(404, "考试不存在")
    return data