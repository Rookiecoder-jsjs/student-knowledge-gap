"""鉴权路由（G11 + auth-roles-design 三角色）：登录 / 建账号 / 班级授权 / 学生账号。

- ``POST /auth/login``：口令换 token（教师或学生；白名单端点，安全模式下也匿名可达）；
- ``GET /auth/me``：会话恢复，返回 role 与主体现形状（前端按角色路由）；
- 账号与授权管理（``POST /auth/teachers`` 等）仅 admin 可用——首个 admin 由
  bootstrap 脚本/命令行创建（scripts/create_teacher.py），避免鸡生蛋；
- 学生自服务账号由 admin 或**本班班主任**开通（``POST /auth/students/{id}/enable``，
  rbac-scopes-design §8 下放）；
- 学科管理员授权（``PUT /auth/teachers/{id}/subject-scopes``）与班主任指派
  （``PUT /auth/classes/{id}/homeroom``）仅 admin；
- 本 router 只做 HTTP 翻译；裁决逻辑在 app.auth。
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth
from app.api.deps import access_ctx, get_db, require_admin, require_teacher
from app.models import Class as ClassModel
from app.models import Student, Teacher

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class TeacherCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    school_id: int
    admin: bool = False
    kb_editor: bool = False


class StudentEnableRequest(BaseModel):
    password: str = Field(min_length=6, max_length=128)
    username: str | None = Field(default=None, min_length=2, max_length=64)


class GrantRequest(BaseModel):
    class_ids: list[int]
    # 可选科任学科（rbac-scopes-design §7）：None=不改动存量行；""=清绑全科；
    # 非空=新授权行带学科 / 存量行改绑
    subject: str | None = Field(default=None, max_length=20)


class KbEditorRequest(BaseModel):
    kb_editor: bool


class SubjectScopeItem(BaseModel):
    subject: str = Field(min_length=1, max_length=20)
    grade: int


class SubjectScopeSetRequest(BaseModel):
    """学科管理员授权（覆盖式设置；rbac-scopes-design §7）。"""

    scopes: list[SubjectScopeItem]


class HomeroomRequest(BaseModel):
    """班主任指派（teacher_id=None = 取消；班级侧单值，rbac-scopes-design §3）。"""

    teacher_id: int | None = None


def _unique_username(db: Session, username: str) -> bool:
    """username 跨 teacher/student 两表唯一（同名登录会歧义，先建先占）。"""
    if db.scalar(select(Teacher.id).where(Teacher.username == username)):
        return False
    if db.scalar(select(Student.id).where(Student.username == username)):
        return False
    return True


def _teacher_payload(db: Session, ctx: auth.AccessContext) -> dict:
    t = ctx.teacher
    classes = [
        {"class_id": c.id, "name": c.name}
        for c in sorted(t.classes, key=lambda c: c.id)
    ]
    scopes = auth.subject_scopes(db, ctx) or []
    return {
        "role": "admin" if t.admin else "teacher",
        "teacher": {"id": t.id, "name": t.name, "admin": t.admin, "kb_editor": t.kb_editor},
        "classes": classes,
        # RBAC 范围（rbac-scopes-design §9）：前端旗标与后端同口径派生
        "subject_scopes": [{"subject": s, "grade": g} for s, g in scopes],
        "homeroom_class_ids": auth.homeroom_class_ids(db, t.id),
    }


def _student_payload(ctx: auth.AccessContext) -> dict:
    s = ctx.student
    return {
        "role": "student",
        "student": {
            "id": s.id,
            "name_or_alias": s.name_or_alias,
            "class_id": s.class_id,
            "class_name": s.clazz.name if s.clazz is not None else None,
        },
    }


@router.post("/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """口令登录 → Bearer token（教师/admin 或学生；开放模式同样可用）。"""
    try:
        principal, token, kind = auth.authenticate_any(db, req.username, req.password)
    except auth.AuthError as e:
        raise HTTPException(401, str(e)) from e
    ctx = (
        auth.AccessContext(teacher=principal)
        if kind == "t"
        else auth.AccessContext(student=principal)
    )
    body = (
        _teacher_payload(db, ctx) if kind == "t" else _student_payload(ctx)
    )
    body["token"] = token
    return body


@router.get("/auth/me")
def me(
    ctx=Depends(access_ctx),
    db: Session = Depends(get_db),
):
    """当前身份（前端会话恢复用；匿名返回 authenticated:false）。"""
    if ctx.principal is None:
        return {"authenticated": False}
    body = (
        _teacher_payload(db, ctx)
        if ctx.teacher is not None
        else _student_payload(ctx)
    )
    body["authenticated"] = True
    return body


@router.post("/auth/teachers")
def create_teacher(
    req: TeacherCreate,
    admin_ctx=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员建教师账号（口令 PBKDF2 落库；建号即进入安全模式）。"""
    if not _unique_username(db, req.username):
        raise HTTPException(400, f"用户名已存在: {req.username}")
    salt = secrets.token_bytes(16)
    t = Teacher(
        school_id=req.school_id,
        name=req.name,
        username=req.username,
        salt=salt,
        password_hash=auth.hash_password(req.password, salt),
        admin=req.admin,
        kb_editor=req.kb_editor,
    )
    db.add(t)
    db.flush()
    return {"teacher_id": t.id}


@router.get("/auth/teachers")
def list_teachers(
    admin_ctx=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员查教师账号（frontend-ends-design §C 账号管理列表面）。

    返回 name/username/admin/已授班级/学科×年级授权/班主任班级——账号管理
    行内编辑的数据源（rbac-scopes-design §7）。
    """
    rows = db.scalars(select(Teacher).order_by(Teacher.id))
    out = []
    for t in rows:
        _tctx = auth.AccessContext(teacher=t)
        scopes = auth.subject_scopes(db, _tctx) or []
        out.append(
            {
                "teacher_id": t.id,
                "name": t.name,
                "username": t.username,
                "admin": bool(t.admin),
                "kb_editor": bool(t.kb_editor),
                "classes": [
                    {"class_id": c.id, "name": c.name}
                    for c in sorted(t.classes, key=lambda c: c.id)
                ],
                "subject_scopes": [{"subject": s, "grade": g} for s, g in scopes],
                "homeroom_class_ids": auth.homeroom_class_ids(db, t.id),
            }
        )
    return {"teachers": out}


@router.post("/auth/students/{student_id}/enable")
def enable_student(
    student_id: int,
    req: StudentEnableRequest,
    ctx=Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """开通/重置学生自服务账号（username 缺省 = external_code 学籍号）。

    RBAC（rbac-scopes-design §8）：admin 或本班班主任——账号开通权下放班主任，
    只管本班（can_enable_student 裁决）。
    """
    stu = db.get(Student, student_id)
    if stu is None:
        raise HTTPException(404, "学生不存在")
    if not auth.can_enable_student(db, ctx, stu):
        raise HTTPException(403, "学生账号开通需要管理员或本班班主任权限")
    try:
        _stu, username = auth.enable_student_login(
            db, student_id, req.password, req.username
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"student_id": student_id, "username": username}


@router.post("/auth/teachers/{teacher_id}/classes")
def grant_classes(
    teacher_id: int,
    req: GrantRequest,
    admin_ctx=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """授予班级访问权（幂等；admin 全班可见无需授权行）。

    RBAC（rbac-scopes-design §7）：可选 subject —— 传「数学」等则该授权行为
    科任（仅见该学科考试）；传空串清空 = 全科（存量语义）。
    """
    t = db.get(Teacher, teacher_id)
    if t is None:
        raise HTTPException(404, "教师不存在")
    bound_subject = req.subject.strip() if req.subject else None
    added = 0
    for cid in req.class_ids:
        if db.get(ClassModel, cid) is None:
            raise HTTPException(404, f"班级 {cid} 不存在")
        row = db.scalar(
            select(auth.TeacherClass).where(
                auth.TeacherClass.teacher_id == teacher_id,
                auth.TeacherClass.class_id == cid,
            )
        )
        if row is None:
            db.add(
                auth.TeacherClass(
                    teacher_id=teacher_id, class_id=cid, subject=bound_subject
                )
            )
            added += 1
        elif req.subject is not None:
            row.subject = bound_subject
    db.flush()
    return {"teacher_id": teacher_id, "added": added}


@router.put("/auth/teachers/{teacher_id}/subject-scopes")
def set_subject_scopes(
    teacher_id: int,
    req: SubjectScopeSetRequest,
    admin_ctx=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """设置学科管理员授权（学科×年级，覆盖式；rbac-scopes-design §7）。

    授权行授予 KB 内容写权与治理权（can_write_kb/can_govern_kb），角色从 DB
    现读，已发 token 立即生效/失效。
    """
    t = db.get(Teacher, teacher_id)
    if t is None:
        raise HTTPException(404, "教师不存在")
    for row in db.scalars(
        select(auth.TeacherSubjectScope).where(
            auth.TeacherSubjectScope.teacher_id == teacher_id
        )
    ):
        db.delete(row)
    seen: set[tuple[str, int]] = set()
    for item in req.scopes:
        key = (item.subject.strip(), item.grade)
        if key in seen:
            continue
        seen.add(key)
        db.add(
            auth.TeacherSubjectScope(
                teacher_id=teacher_id, subject=key[0], grade=key[1]
            )
        )
    db.flush()
    return {
        "teacher_id": teacher_id,
        "subject_scopes": [{"subject": s, "grade": g} for s, g in sorted(seen)],
    }


@router.put("/auth/classes/{class_id}/homeroom")
def set_homeroom(
    class_id: int,
    req: HomeroomRequest,
    admin_ctx=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """指派/取消班主任（一班一人；rbac-scopes-design §3/§7）。

    班主任自动获得本班全科视野与学生账号开通权（无需 teacher_class 授权行）。
    """
    clazz = db.get(ClassModel, class_id)
    if clazz is None:
        raise HTTPException(404, "班级不存在")
    if req.teacher_id is not None and db.get(Teacher, req.teacher_id) is None:
        raise HTTPException(404, "教师不存在")
    clazz.homeroom_teacher_id = req.teacher_id
    db.flush()
    return {"class_id": class_id, "homeroom_teacher_id": req.teacher_id}


@router.post("/auth/teachers/{teacher_id}/kb-editor")
def set_teacher_kb_editor(
    teacher_id: int,
    req: KbEditorRequest,
    admin_ctx=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """授予/撤销知识库编辑权（两层写权的授权面）：内容层写入资格，版本治理仍 admin。

    角色从 DB 现读（token 无状态），已发 token 立即生效/失效。
    """
    t = db.get(Teacher, teacher_id)
    if t is None:
        raise HTTPException(404, "教师不存在")
    t.kb_editor = req.kb_editor
    db.flush()
    return {"teacher_id": teacher_id, "kb_editor": bool(t.kb_editor)}
